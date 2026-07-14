import random
import datetime
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Flag, GameEvent, ScoreAudit, Host
from scorebot_core_lite.auth import verify_cli_token, verify_admin_token
from scorebot_core_lite.scoring.notifications import send_notification

router = APIRouter()

@router.post("/api/flags", dependencies=[Depends(verify_cli_token)])
async def capture_flag(request: Request):
    """Submit a captured flag."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("token")
        flag_val = body.get("flag")

        if not team_token or not flag_val:
            raise HTTPException(status_code=400, detail="Missing token or flag")

        # Lock the attacker team row before reading game status or modifying score,
        # to prevent lost-update races with concurrent score_round ticks.
        attacker = session.query(GameTeam).filter(
            GameTeam.token == team_token
        ).with_for_update().first()
        if not attacker:
            raise HTTPException(status_code=403, detail="Invalid Team Token")

        if attacker.game.status != 1:
            raise HTTPException(status_code=403, detail="Game is not running")

        # Lock the flag row to serialise concurrent capture attempts of the same flag.
        # Any concurrent request will block here until this transaction commits, then
        # see captured_team_id is set and return "already captured".
        flag = session.query(Flag).filter(
            Flag.flag == flag_val,
            Flag.enabled == True
        ).with_for_update().first()

        if not flag or flag.team_id == attacker.id or flag.team.game_id != attacker.game_id:
            raise HTTPException(status_code=404, detail="Flag not valid")

        # 3. Check if already captured
        if flag.captured_team_id is not None:
            return {"status": "success", "message": "Flag already captured"}

        # 4. Lock the victim team row before modifying their score.
        victim = session.query(GameTeam).filter(
            GameTeam.id == flag.team_id
        ).with_for_update().first()

        # 5. Perform capture
        flag.captured_team_id = attacker.id

        # Deduct from victim
        game = attacker.game
        if game.flag_stolen_rate > 0:
            stolen_amt = game.flag_stolen_rate
        else:
            stolen_amt = flag.value * game.flag_captured_multiplier

        victim.score_flags -= stolen_amt
        session.add(ScoreAudit(
            team_id=victim.id,
            source="FLAG-STOLEN",
            amount=-stolen_amt,
            description=f"Flag stolen by {attacker.name}"
        ))

        # Add to attacker
        capture_amt = flag.value * game.flag_captured_multiplier
        attacker.score_flags += capture_amt
        session.add(ScoreAudit(
            team_id=attacker.id,
            source="FLAG-CAPTURE",
            amount=capture_amt,
            description=f"Captured flag from {victim.name}"
        ))

        # Add Event
        event_msg = f"A Flag from {victim.name} was stolen by {attacker.name}!"
        # Create event record
        event = GameEvent(
            game_id=game.id,
            timeout=datetime.datetime.utcnow() + datetime.timedelta(seconds=30),
            data=f'{{"text": "{event_msg}"}}',
            type=1
        )
        session.add(event)
        session.commit()

        # Send Pluggable Notifications
        send_notification(event_msg)

        # 5. Hint response (optional description of another uncaptured flag from the same victim)
        victim_flags = session.query(Flag).filter(
            Flag.team_id == victim.id,
            Flag.enabled == True,
            Flag.captured_team_id == None
        ).all()

        if victim_flags:
            hint_flag = random.choice(victim_flags)
            return {"message": hint_flag.description}

        return {"status": "success", "message": "Flag captured"}
    finally:
        session.close()

# Legacy Compatibility Endpoints
@router.post("/api/flag", dependencies=[Depends(verify_cli_token)])
@router.post("/api/flag/", dependencies=[Depends(verify_cli_token)])
async def legacy_capture_flag(request: Request):
    """Legacy endpoint for submitting a captured flag."""
    return await capture_flag(request)


class AdminFlagCreateSchema(BaseModel):
    name: str
    flag: str
    value: int = 100
    description: str = ""


@router.post("/api/admin/games/{game_id}/hosts/{host_id}/flags", dependencies=[Depends(verify_admin_token)])
def register_flag(game_id: int, host_id: int, data: AdminFlagCreateSchema):
    """Admin endpoint to dynamically register a generated flag for a host."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        if not host.team or host.team.game_id != game_id:
            raise HTTPException(status_code=400, detail="Host team does not belong to the specified game")

        # Check if flag string already exists to avoid duplicates
        existing = session.query(Flag).filter(Flag.flag == data.flag).first()
        if existing:
            return {
                "status": "success",
                "flag_id": existing.id,
                "message": "Flag already registered"
            }

        # Check if a flag with the same name on the same host already exists
        existing_by_name = session.query(Flag).filter(
            Flag.host_id == host_id,
            Flag.name == data.name
        ).first()

        if existing_by_name:
            existing_by_name.flag = data.flag
            existing_by_name.description = data.description
            existing_by_name.value = data.value
            existing_by_name.enabled = True
            existing_by_name.captured_team_id = None
            session.commit()
            session.refresh(existing_by_name)
            return {
                "status": "success",
                "flag_id": existing_by_name.id,
                "message": f"Flag '{data.name}' updated successfully for host {host.fqdn}"
            }

        flag = Flag(
            name=data.name,
            flag=data.flag,
            enabled=True,
            description=data.description,
            value=data.value,
            host_id=host_id,
            team_id=host.team_id
        )
        session.add(flag)
        session.commit()
        session.refresh(flag)
        return {
            "status": "success",
            "flag_id": flag.id,
            "message": f"Flag '{data.name}' registered successfully for host {host.fqdn}"
        }
    finally:
        session.close()

