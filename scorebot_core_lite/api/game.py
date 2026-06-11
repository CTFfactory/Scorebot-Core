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
import io
import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from PIL import Image

from scorebot_core_lite import config, ingest
from scorebot_core_lite.auth import verify_admin_token
from scorebot_core_lite.models import Game, GameTeam, Host, Service, SessionLocal, ScoreAdjustment

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
            if team_spec.name.lower() == "gold":
                logger.info("Skipping import of team '%s' (Gold Team)", team_spec.name)
                continue
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
        "gold":  0xFFB300,   # Material Amber 600
        "red":   0xE53935,   # Material Red 600
        "green": 0x43A047,   # Material Green 600
        "white": 0xEEEEEE,
        "black": 0x212121,
    }
    if color_hint.lower() == "blue":
        import hashlib
        import colorsys
        h_val = int(hashlib.md5(team_name.encode()).hexdigest(), 16)
        h = 180 + (h_val % 81)  # 180 to 260 (cyan-blue-indigo)
        s = 0.70 + (((h_val >> 8) % 21) / 100.0)  # 70% to 90%
        l = 0.45 + (((h_val >> 16) % 16) / 100.0)  # 45% to 60%
        r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
        return (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)

    if color_hint.lower() in _palette:
        return _palette[color_hint.lower()]
    # Deterministic fallback: hash team name to 24-bit color
    import hashlib
    return int(hashlib.md5(team_name.encode()).hexdigest(), 16) & 0xFFFFFF


# ---------------------------------------------------------------------------
# Game Configuration and Editing Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/admin/games/{game_id}/details", dependencies=[Depends(verify_admin_token)])
def get_game_details(game_id: int):
    """Retrieve full nested structure of a game, including teams, hosts, services, and parameters."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        
        teams_list = []
        for team in game.teams:
            hosts_list = []
            for host in team.hosts:
                services_list = []
                for svc in host.services:
                    services_list.append({
                        "id": svc.id,
                        "port": svc.port,
                        "name": svc.name,
                        "value": svc.value,
                        "protocol": svc.protocol,  # 1=tcp, 2=udp
                        "status": svc.status,
                        "application": svc.application
                    })
                hosts_list.append({
                    "id": host.id,
                    "fqdn": host.fqdn,
                    "name": host.name,
                    "ip": host.ip,
                    "online": host.online,
                    "services": services_list
                })
            teams_list.append({
                "id": team.id,
                "name": team.name,
                "subnet": team.subnet,
                "color": team.color,
                "offensive": team.offensive,
                "minimal": team.minimal,
                "token": team.token,
                "visible": team.visible is not False,
                "score": team.get_score(),
                "score_adjustments": [
                    {
                        "id": adj.id,
                        "amount": adj.amount,
                        "reason": adj.reason,
                        "timestamp": adj.timestamp.isoformat() if adj.timestamp else None
                    } for adj in team.score_adjustments
                ],
                "hosts": hosts_list
            })
            
        return {
            "id": game.id,
            "name": game.name,
            "mode": game.mode,
            "status": game.status,
            "round_time": game.round_time,
            "job_timeout": game.job_timeout,
            "job_cleanup_time": game.job_cleanup_time,
            "flag_stolen_rate": game.flag_stolen_rate,
            "flag_captured_multiplier": game.flag_captured_multiplier,
            "beacon_value": game.beacon_value,
            "ticket_cost": game.ticket_cost,
            "ticket_max_score": game.ticket_max_score,
            "ticket_grace_period": game.ticket_grace_period,
            "ticket_max_scoring": game.ticket_max_scoring,
            "ticket_reopen_multiplier": game.ticket_reopen_multiplier,
            "score_exchange_rate": game.score_exchange_rate,
            "host_ping_ratio": game.host_ping_ratio,
            "zero_out_time": game.zero_out_time.isoformat() if game.zero_out_time else None,
            "teams": teams_list
        }
    finally:
        session.close()


class GameParametersSchema(BaseModel):
    round_time: Optional[int] = None
    job_timeout: Optional[int] = None
    job_cleanup_time: Optional[int] = None
    flag_stolen_rate: Optional[int] = None
    flag_captured_multiplier: Optional[int] = None
    beacon_value: Optional[int] = None
    ticket_cost: Optional[int] = None
    ticket_max_score: Optional[int] = None
    ticket_grace_period: Optional[int] = None
    ticket_max_scoring: Optional[int] = None
    ticket_reopen_multiplier: Optional[int] = None
    score_exchange_rate: Optional[int] = None
    host_ping_ratio: Optional[int] = None


@router.put("/api/admin/games/{game_id}/parameters", dependencies=[Depends(verify_admin_token)])
def update_game_parameters(game_id: int, data: GameParametersSchema):
    """Update general game settings/parameters."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(game, field, value)
        session.commit()
        return {"status": "success", "message": "Parameters updated"}
    finally:
        session.close()


class TeamUpdateSchema(BaseModel):
    subnet: Optional[str] = None
    color: Optional[int] = None
    offensive: Optional[bool] = None
    minimal: Optional[bool] = None
    visible: Optional[bool] = None


@router.put("/api/admin/games/teams/{team_id}", dependencies=[Depends(verify_admin_token)])
def update_team(team_id: int, data: TeamUpdateSchema):
    """Update team-specific details (subnet, color, minimal mode)."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(team, field, value)
        session.commit()
        return {"status": "success", "message": "Team updated"}
    finally:
        session.close()


class HostCreateSchema(BaseModel):
    fqdn: str
    name: Optional[str] = None
    ip: Optional[str] = ""


class HostUpdateSchema(BaseModel):
    fqdn: Optional[str] = None
    name: Optional[str] = None
    ip: Optional[str] = None
    online: Optional[bool] = None


@router.post("/api/admin/games/teams/{team_id}/hosts", dependencies=[Depends(verify_admin_token)])
def create_host(team_id: int, data: HostCreateSchema):
    """Create a new host inside a team."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        
        name = data.name or data.fqdn.split(".")[0]
        host = Host(fqdn=data.fqdn, name=name, ip=data.ip, team_id=team_id, online=False)
        session.add(host)
        session.commit()
        session.refresh(host)
        return {"status": "success", "host_id": host.id, "message": "Host created"}
    finally:
        session.close()


@router.put("/api/admin/games/hosts/{host_id}", dependencies=[Depends(verify_admin_token)])
def update_host(host_id: int, data: HostUpdateSchema):
    """Update host attributes (FQDN, name, IP)."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(host, field, value)
        session.commit()
        return {"status": "success", "message": "Host updated"}
    finally:
        session.close()


@router.delete("/api/admin/games/hosts/{host_id}", dependencies=[Depends(verify_admin_token)])
def delete_host(host_id: int):
    """Delete a host and all its services."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        session.delete(host)
        session.commit()
        return {"status": "success", "message": "Host deleted"}
    finally:
        session.close()


class ServiceCreateSchema(BaseModel):
    port: int
    name: str
    value: int = 50
    protocol: int = 1 # 1=tcp, 2=udp
    application: str = "ping"


class ServiceUpdateSchema(BaseModel):
    port: Optional[int] = None
    name: Optional[str] = None
    value: Optional[int] = None
    protocol: Optional[int] = None
    application: Optional[str] = None
    status: Optional[int] = None


@router.post("/api/admin/games/hosts/{host_id}/services", dependencies=[Depends(verify_admin_token)])
def create_service(host_id: int, data: ServiceCreateSchema):
    """Create a new service on a host."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        
        service = Service(
            port=data.port,
            name=data.name[:64],
            value=data.value,
            protocol=data.protocol,
            application=data.application[:64],
            host_id=host_id,
            status=2 # offline/timeout by default
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        return {"status": "success", "service_id": service.id, "message": "Service created"}
    finally:
        session.close()


@router.put("/api/admin/games/services/{service_id}", dependencies=[Depends(verify_admin_token)])
def update_service(service_id: int, data: ServiceUpdateSchema):
    """Update service details (port, points, name, status, etc.)."""
    session = SessionLocal()
    try:
        service = session.query(Service).filter(Service.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "name" or field == "application":
                value = value[:64]
            setattr(service, field, value)
        session.commit()
        return {"status": "success", "message": "Service updated"}
    finally:
        session.close()


@router.delete("/api/admin/games/services/{service_id}", dependencies=[Depends(verify_admin_token)])
def delete_service(service_id: int):
    """Delete a service."""
    session = SessionLocal()
    try:
        service = session.query(Service).filter(Service.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        session.delete(service)
        session.commit()
        return {"status": "success", "message": "Service deleted"}
    finally:
        session.close()


class TeamCreateSchema(BaseModel):
    name: str
    subnet: Optional[str] = ""
    color: Optional[int] = 0
    offensive: Optional[bool] = False
    minimal: Optional[bool] = False
    visible: Optional[bool] = True


@router.post("/api/admin/games/{game_id}/teams", dependencies=[Depends(verify_admin_token)])
def create_team(game_id: int, data: TeamCreateSchema):
    """Manually add a team to a game (e.g. Red Team)."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        
        # Idempotency check: if team already exists, return success info
        existing = session.query(GameTeam).filter(GameTeam.game_id == game_id, GameTeam.name == data.name).first()
        if existing:
            return {
                "status": "success",
                "team_id": existing.id,
                "team_name": existing.name,
                "token": existing.token,
                "message": "Team already exists"
            }

        # Determine color
        color = data.color if data.color else _team_color(data.name, data.name)
        
        team = GameTeam(
            name=data.name,
            subnet=data.subnet,
            color=color,
            offensive=data.offensive,
            minimal=data.minimal,
            visible=data.visible,
            game_id=game_id
        )
        session.add(team)
        session.commit()
        session.refresh(team)
        return {
            "status": "success",
            "team_id": team.id,
            "team_name": team.name,
            "token": team.token,
            "message": "Team created successfully"
        }
    finally:
        session.close()


@router.delete("/api/admin/games/teams/{team_id}", dependencies=[Depends(verify_admin_token)])
def delete_team(team_id: int):
    """Delete a team and its associated hosts/services."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        session.delete(team)
        session.commit()
        return {"status": "success", "message": "Team deleted"}
    finally:
        session.close()


class ScoreAdjustmentSchema(BaseModel):
    amount: int
    reason: str


@router.post("/api/admin/games/teams/{team_id}/adjust-score", dependencies=[Depends(verify_admin_token)])
def adjust_team_score(team_id: int, data: ScoreAdjustmentSchema):
    """Adjust a team's score with a specified reason."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        
        adj = ScoreAdjustment(
            team_id=team_id,
            amount=data.amount,
            reason=data.reason
        )
        session.add(adj)
        session.commit()
        return {"status": "success", "message": "Score adjusted successfully"}
    finally:
        session.close()


class ScheduleZeroOutSchema(BaseModel):
    zero_out_time: Optional[str] = None  # ISO format string or None to cancel


@router.post("/api/admin/games/{game_id}/zero-scores", dependencies=[Depends(verify_admin_token)])
def zero_game_scores_endpoint(game_id: int):
    """Immediately zero out scores for a game."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        from scorebot_core_lite.scoring.engine import zero_game_scores
        zero_game_scores(session, game_id)
        return {"status": "success", "message": "All team scores successfully zeroed out"}
    finally:
        session.close()


@router.post("/api/admin/games/{game_id}/schedule-zero-out", dependencies=[Depends(verify_admin_token)])
def schedule_zero_out_endpoint(game_id: int, data: ScheduleZeroOutSchema):
    """Schedule a score zero-out for a game."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        
        if data.zero_out_time:
            try:
                # Parse ISO format datetime
                dt = datetime.datetime.fromisoformat(data.zero_out_time.replace("Z", "+00:00")).replace(tzinfo=None)
                game.zero_out_time = dt
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"Invalid datetime format: {e}")
        else:
            game.zero_out_time = None
        
        session.commit()
        return {
            "status": "success", 
            "message": f"Zero-out time scheduled to {game.zero_out_time}" if game.zero_out_time else "Zero-out schedule cancelled"
        }
    finally:
        session.close()


@router.post("/api/admin/games/teams/{team_id}/logo", dependencies=[Depends(verify_admin_token)])
async def upload_team_logo(team_id: int, file: UploadFile = File(...)):
    """Upload and validate a new team logo image.
    
    Validates file size (max 2MB), magic bytes/extension, and image dimensions
    (must be square 1:1, minimum 300x300 pixels). 
    SVG files are parsed securely to reject scripts/inline JS events (XSS/XXE mitigation).
    """
    # 1. Check if team exists
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        
        # 2. Validate file extension
        filename = file.filename or ""
        ext = os.path.splitext(filename.lower())[1].lstrip('.')
        allowed_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
        if ext not in allowed_exts:
            raise HTTPException(status_code=400, detail=f"Invalid file extension. Allowed: {', '.join(allowed_exts)}")

        # 3. Read up to 2MB + 1 byte chunk by chunk to check size
        max_bytes = 2 * 1024 * 1024
        file_bytes = bytearray()
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            file_bytes.extend(chunk)
            if len(file_bytes) > max_bytes:
                raise HTTPException(status_code=413, detail="File too large. Maximum size allowed is 2 MB.")

        # 4. Check magic bytes / headers to prevent extension spoofing
        # And validate dimensions
        if ext == 'svg':
            # Parse SVG securely to prevent XXE / XSS
            try:
                # ET.fromstring in python 3 does not resolve external entities by default
                root = ET.fromstring(file_bytes)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid SVG file format.")
            
            # recursive XSS prevention
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag.lower() == 'script':
                    raise HTTPException(status_code=400, detail="Security validation failed: SVG contains <script> tag.")
                for attr, val in elem.attrib.items():
                    attr_name = attr.split('}')[-1] if '}' in attr else attr
                    if attr_name.lower().startswith('on'):
                        raise HTTPException(status_code=400, detail="Security validation failed: SVG contains inline JavaScript event handler.")
                    if attr_name.lower() in ('href', 'xlink:href') and val.lower().strip().startswith('javascript:'):
                        raise HTTPException(status_code=400, detail="Security validation failed: SVG contains a javascript: link.")

            # Validate aspect ratio / dimensions
            width_val = root.attrib.get('width')
            height_val = root.attrib.get('height')
            viewbox_val = root.attrib.get('viewBox')
            
            is_square = False
            min_size_ok = False
            
            if width_val and height_val:
                try:
                    def clean_unit(v):
                        return float(''.join(c for c in v if c.isdigit() or c == '.'))
                    w = clean_unit(width_val)
                    h = clean_unit(height_val)
                    is_square = (w == h)
                    min_size_ok = (w >= 300)
                except Exception:
                    pass
            
            if not is_square and viewbox_val:
                try:
                    parts = [float(x) for x in viewbox_val.strip().split()]
                    if len(parts) == 4:
                        w = parts[2]
                        h = parts[3]
                        is_square = (w == h)
                        min_size_ok = (w >= 300)
                except Exception:
                    pass
            
            if not is_square:
                raise HTTPException(status_code=400, detail="Image validation failed: SVG must have a square aspect ratio (1:1) in width/height or viewBox.")
            if not min_size_ok:
                raise HTTPException(status_code=400, detail="Image validation failed: SVG must have a minimum resolution of 300x300 pixels in width/height or viewBox.")
        
        else:
            # Raster images (PNG, JPEG, GIF, WebP)
            # Verify Magic Bytes
            # PNG starts with 89 50 4E 47
            # JPEG starts with FF D8 FF
            # GIF starts with 47 49 46 38
            # WebP starts with 52 49 46 46 (RIFF) ... 57 45 42 50 (WEBP)
            magic_ok = False
            if ext == 'png' and file_bytes[:4] == b'\x89PNG':
                magic_ok = True
            elif ext in ('jpg', 'jpeg') and file_bytes[:3] == b'\xff\xd8\xff':
                magic_ok = True
            elif ext == 'gif' and file_bytes[:4] == b'GIF8':
                magic_ok = True
            elif ext == 'webp' and file_bytes[:4] == b'RIFF' and b'WEBP' in file_bytes[8:16]:
                magic_ok = True
                
            if not magic_ok:
                raise HTTPException(status_code=400, detail=f"Magic bytes mismatch: file does not match target {ext} format.")
            
            try:
                img = Image.open(io.BytesIO(file_bytes))
                img.verify()
                # Re-open after verify() because verify() invalidates the image object
                img = Image.open(io.BytesIO(file_bytes))
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid image file format.")
            
            # Check aspect ratio (must be square 1:1)
            if img.width != img.height:
                raise HTTPException(status_code=400, detail=f"Image validation failed: Image must be a perfect square (1:1 aspect ratio). Uploaded dimensions: {img.width}x{img.height} pixels.")
            
            # Check minimum size (must be at least 300x300)
            if img.width < 300:
                raise HTTPException(status_code=400, detail=f"Image validation failed: Image must be at least 300x300 pixels. Uploaded dimensions: {img.width}x{img.height} pixels.")

        # 5. Create path and save file securely
        # Sanitize name based on team ID to prevent path traversal
        logo_dir = os.path.join(config.STATIC_DIR, "img", "team")
        os.makedirs(logo_dir, exist_ok=True)
        
        logo_filename = f"team_{team_id}.{ext}"
        logo_path = os.path.join(logo_dir, logo_filename)
        
        with open(logo_path, "wb") as f:
            f.write(file_bytes)
        
        # 6. Update database model
        # The scoreboard expects path like static/img/team/team_{team_id}.{ext}
        relative_path = f"static/img/team/{logo_filename}"
        team.logo = relative_path
        session.commit()
        
        return {
            "status": "success",
            "message": "Logo uploaded successfully",
            "logo_path": relative_path
        }
    finally:
        session.close()



