# Copyright (C) 2020 iDigitalFlame
# Copyright (C) 2026 luftegrof
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import datetime
import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from scorebot_core_lite import config, ingest
from scorebot_core_lite.auth import verify_admin_token
from scorebot_core_lite.models import Game, GameTeam, Host, Service, SessionLocal

router = APIRouter()
logger = logging.getLogger("scorebot_core_lite.api.game")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GameCreateSchema(BaseModel):
    name: str
    mode: int = 0


class GameImportSchema(BaseModel):
    """Request body for POST /api/admin/games/import.

    Two modes:
      - DB mode (primary): Supply 'game_definitions_db' with the full compiled
        game-definitions JSON (decoded from VAR_GAME_DEFINITIONS_DB). The
        GitHub Action extracts this in the deploy workflow and sends it here.
      - Disk mode (fallback/dev): Omit 'game_definitions_db'; the server reads
        from GAME_DEFINITIONS_PATH on disk.
    """
    event: str
    environment: str
    subnets: Optional[Dict[str, str]] = None       # {"team_name": "10.x.x.0/24"}
    game_definitions_db: Optional[Dict] = None     # Full compiled DB from VAR_GAME_DEFINITIONS_DB
    mode: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/admin/games", dependencies=[Depends(verify_admin_token)])
def create_game(data: GameCreateSchema):
    """Manually create a named game (no teams or hosts)."""
    session = SessionLocal()
    try:
        game = Game(name=data.name, mode=data.mode, status=0)
        session.add(game)
        session.commit()
        session.refresh(game)
        return {"id": game.id, "name": game.name, "status": game.status}
    finally:
        session.close()


@router.post("/api/admin/games/import", dependencies=[Depends(verify_admin_token)])
def import_game(data: GameImportSchema):
    """Import a complete game profile from game-definitions files.

    Reads event/{event}.json and role/*.json from the GAME_DEFINITIONS_PATH
    directory to construct the game, teams, host stubs, and services in one
    transaction. This replaces the legacy Django /sb2import/ CSRF workflow.

    If a game with the same name already exists it is returned as-is (idempotent).
    """
    # Build import spec — prefer compiled DB if provided, fall back to disk.
    try:
        if data.game_definitions_db:
            logger.info(
                "Importing game '%s' from compiled game-definitions DB",
                data.event,
            )
            spec = ingest.load_from_db(
                db=data.game_definitions_db,
                event_name=data.event,
                environment=data.environment,
                subnets=data.subnets,
            )
        else:
            logger.info(
                "Importing game '%s' from disk at %s",
                data.event, config.GAME_DEFINITIONS_PATH,
            )
            spec = ingest.load_from_disk(
                event_name=data.event,
                environment=data.environment,
                subnets=data.subnets,
            )
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid game-definitions: {exc}")

    session = SessionLocal()
    try:
        # Idempotency: if this game name already exists, return it.
        existing = session.query(Game).filter(Game.name == spec.game_name).first()
        if existing:
            logger.info("Game '%s' already exists (id=%d), skipping import", spec.game_name, existing.id)
            return {
                "game_id": existing.id,
                "game_name": existing.name,
                "status": "exists",
                "message": "Game already imported; use /api/admin/games/{id}/start to begin scoring.",
            }

        # Parse optional schedule timestamps.
        def _parse_dt(s):
            if not s:
                return None
            try:
                return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                return None

        # Create the Game row.
        game = Game(
            name=spec.game_name,
            mode=data.mode,
            status=0,
            start=_parse_dt(spec.game_start),
            finish=_parse_dt(spec.game_end),
        )
        session.add(game)
        session.flush()  # get game.id without committing

        hosts_created = 0
        services_created = 0

        for team_spec in spec.teams:
            # Assign a stable integer color from team name hash (0x000000–0xFFFFFF).
            # Blue teams get blue-ish hue; can be overridden via admin UI later.
            color_int = _team_color(team_spec.name, team_spec.color)

            team = GameTeam(
                name=team_spec.name,
                subnet=team_spec.subnet or "",
                color=color_int,
                offensive=False,
                minimal=False,
                game_id=game.id,
            )
            session.add(team)
            session.flush()  # get team.id

            for host_spec in team_spec.hosts:
                host = Host(
                    fqdn=host_spec.fqdn,
                    name=host_spec.fqdn.split(".")[0],  # short hostname portion
                    ip="",          # filled in by /api/hosts when host boots
                    online=False,
                    team_id=team.id,
                )
                session.add(host)
                session.flush()  # get host.id
                hosts_created += 1

                for svc_spec in host_spec.services:
                    proto = 2 if svc_spec.protocol == "UDP" else 1
                    svc = Service(
                        port=svc_spec.port,
                        name=svc_spec.name[:64],
                        value=svc_spec.points,
                        bonus=False,
                        application="ping",
                        protocol=proto,
                        status=2,  # timeout / offline until first score
                        host_id=host.id,
                    )
                    session.add(svc)
                    services_created += 1

        session.commit()

        logger.info(
            "Imported game '%s' (id=%d): %d teams, %d host stubs, %d services",
            spec.game_name, game.id,
            len(spec.teams), hosts_created, services_created,
        )

        return {
            "game_id": game.id,
            "game_name": spec.game_name,
            "status": "created",
            "teams": len(spec.teams),
            "hosts_created": hosts_created,
            "services_created": services_created,
            "message": (
                f"Game '{spec.game_name}' imported successfully. "
                "Use POST /api/admin/games/{game_id}/start to begin scoring."
            ),
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.post("/api/admin/games/{game_id}/start", dependencies=[Depends(verify_admin_token)])
def start_game(game_id: int):
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        game.status = 1
        if not game.start:
            game.start = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        session.commit()
        return {"status": "started", "game_id": game_id}
    finally:
        session.close()


@router.post("/api/admin/games/{game_id}/pause", dependencies=[Depends(verify_admin_token)])
def pause_game(game_id: int):
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        game.status = 2
        session.commit()
        return {"status": "paused", "game_id": game_id}
    finally:
        session.close()


@router.post("/api/admin/games/{game_id}/stop", dependencies=[Depends(verify_admin_token)])
def stop_game(game_id: int):
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        game.status = 3
        game.finish = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        session.commit()
        return {"status": "stopped", "game_id": game_id}
    finally:
        session.close()


@router.get("/api/games", dependencies=[Depends(verify_admin_token)])
def list_games():
    """List all games."""
    session = SessionLocal()
    try:
        games = session.query(Game).all()
        return [g.get_list_json() for g in games]
    finally:
        session.close()


@router.get("/api/admin/games/{game_id}", dependencies=[Depends(verify_admin_token)])
def get_game(game_id: int):
    """Get details for a single game including team and host counts."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        detail = game.get_list_json()
        detail["teams"] = len(game.teams)
        detail["hosts"] = sum(len(t.hosts) for t in game.teams)
        return detail
    finally:
        session.close()


@router.delete("/api/admin/games/{game_id}", dependencies=[Depends(verify_admin_token)])
def delete_game(game_id: int):
    """Delete a game and all its associated data (teams, hosts, services, flags, etc.).

    Use this to reset a game before re-importing updated game-definitions.
    The import endpoint is a hard skip if the game already exists, so a clean
    delete is the correct way to force a re-import:

        DELETE /api/admin/games/{id}
        POST   /api/admin/games/import
        POST   /api/admin/games/{new_id}/start
    """
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        if game.status == 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete a running game. "
                    "Pause or stop it first with POST /api/admin/games/{id}/pause."
                ),
            )
        session.delete(game)
        session.commit()
        logger.info("Deleted game id=%d ('%s')", game_id, game.name)
        return {"status": "deleted", "game_id": game_id, "game_name": game.name}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _team_color(team_name: str, color_hint: str) -> int:
    """Derive a stable 24-bit integer color for a team.

    Blue teams get a blue-shifted palette; gold teams get amber; red teams
    get red. Falls back to a hash of the team name for unknown colors.
    This can always be overridden via the admin UI after import.
    """
    _palette = {
        "blue":  0x1E88E5,   # Material Blue 600
        "gold":  0xFFB300,   # Material Amber 600
        "red":   0xE53935,   # Material Red 600
        "green": 0x43A047,   # Material Green 600
        "white": 0xEEEEEE,
        "black": 0x212121,
    }
    if color_hint.lower() in _palette:
        return _palette[color_hint.lower()]
    # Deterministic fallback: hash team name to 24-bit color
    return hash(team_name) & 0xFFFFFF
