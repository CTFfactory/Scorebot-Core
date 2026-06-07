import datetime
import json
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, Game, GameEvent
from scorebot_core_lite.auth import verify_admin_token
from scorebot_core_lite.scoring.notifications import send_notification

router = APIRouter()

@router.post("/api/messages", dependencies=[Depends(verify_admin_token)])
async def post_message(request: Request):
    """Post a message to the scoreboard and to external webhooks."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        game_id = body.get("game_id")
        text = body.get("text")

        if not game_id or not text:
            raise HTTPException(status_code=400, detail="Missing game_id or text")

        game = session.query(Game).filter(Game.id == int(game_id)).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        # Create a GameEvent of type 0 (Message) for the scoreboard
        event_data = json.dumps({"text": text})
        event = GameEvent(
            game_id=game.id,
            timeout=datetime.datetime.utcnow() + datetime.timedelta(seconds=60),
            data=event_data,
            type=0
        )
        session.add(event)
        session.commit()

        # Send to webhooks (Slack, Discord, Twitter)
        send_notification(text)

        return {"status": "success", "message": "Message posted"}
    finally:
        session.close()
