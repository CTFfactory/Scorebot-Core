from fastapi import APIRouter, HTTPException, Depends
from scorebot_core_lite.models import SessionLocal, Game
from scorebot_core_lite.auth import verify_cli_token

router = APIRouter()

@router.get("/api/mapper/{game_id}", dependencies=[Depends(verify_cli_token)])
@router.get("/api/mapper/{game_id}/", dependencies=[Depends(verify_cli_token)])
def get_uuid_mapping(game_id: int):
    """Retrieve UUID to Team name mappings for a running game."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            # Fall back to the active running game if the requested ID does not exist.
            # This handles cases where external components are configured with a hardcoded game ID.
            game = session.query(Game).filter(Game.status == 1).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        if game.status != 1:
            raise HTTPException(status_code=403, detail="Game is not running")

        return {"teams": [t.get_json_mapper() for t in game.teams]}
    finally:
        session.close()
