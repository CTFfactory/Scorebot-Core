from fastapi import APIRouter, HTTPException, Depends
from scorebot_core_lite.models import SessionLocal, Game
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/api/games")
def get_games():
    """Retrieve list of all games."""
    session = SessionLocal()
    try:
        games = session.query(Game).order_by(Game.start).all()
        return [g.get_list_json() for g in games]
    finally:
        session.close()

@router.get("/api/games/{game_id}/scoreboard")
def get_game_scoreboard(game_id: int):
    """Retrieve full JSON scoreboard data for a game."""
    session = SessionLocal()
    try:
        game = session.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        return game.get_json_scoreboard()
    finally:
        session.close()
