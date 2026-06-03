import random
import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Flag, GameEvent
from scorebot_core_lite.auth import verify_cli_token
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

        # 1. Authenticate attacker team
        attacker = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not attacker:
            raise HTTPException(status_code=403, detail="Invalid Team Token")

        if attacker.game.status != 1:
            raise HTTPException(status_code=403, detail="Game is not running")

        # 2. Look up the flag
        flag = session.query(Flag).filter(
            Flag.flag == flag_val,
            Flag.enabled == True
        ).first()

        if not flag or flag.team_id == attacker.id or flag.team.game_id != attacker.game_id:
            raise HTTPException(status_code=404, detail="Flag not valid")

        # 3. Check if already captured
        if flag.captured_team_id is not None:
            return {"status": "success", "message": "Flag already captured"}

        # 4. Perform capture
        flag.captured_team_id = attacker.id

        # Deduct from victim
        victim = flag.team
        game = attacker.game
        if game.flag_stolen_rate > 0:
            victim.score_flags -= game.flag_stolen_rate
        else:
            victim.score_flags -= flag.value * game.flag_captured_multiplier

        # Add to attacker
        attacker.score_flags += flag.value * game.flag_captured_multiplier

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
