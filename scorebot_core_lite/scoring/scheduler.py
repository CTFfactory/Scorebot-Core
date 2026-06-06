import time
import threading
import logging
from datetime import datetime, timezone
from scorebot_core_lite import config
from scorebot_core_lite.models import SessionLocal, Game
from scorebot_core_lite.scoring.engine import score_round
from scorebot_core_lite.scoring.cleanup import run_cleanup

logger = logging.getLogger("scorebot_core_lite.scheduler")

class SchedulerDaemon(threading.Thread):
    """Background scheduler executing scoring and cleanup tasks at intervals."""

    def __init__(self):
        super().__init__(daemon=True, name="ScorebotScheduler")
        self.running = False
        self.last_scoring = 0.0
        self.last_cleanup = 0.0
        logger.info("Scorebot Background Scheduler initialized.")

    def run(self):
        self.running = True
        logger.info("Scorebot Background Scheduler thread started.")

        while self.running:
            now = time.time()
            session = None
            try:
                session = SessionLocal()
                # 0. Check scheduled zero-outs
                utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
                from scorebot_core_lite.scoring.engine import zero_game_scores
                games_to_zero = session.query(Game).filter(Game.zero_out_time != None).all()
                for game in games_to_zero:
                    if game.zero_out_time and utc_now >= game.zero_out_time:
                        logger.info(f"Zeroing out scores for game {game.name} as scheduled zero_out_time {game.zero_out_time} was reached.")
                        zero_game_scores(session, game.id)
                        game.zero_out_time = None
                session.commit()

                # 1. Check scoring interval
                if now - self.last_scoring >= config.SCORING_INTERVAL:
                    running_games = session.query(Game).filter(Game.status == 1).all()
                    for game in running_games:
                        score_round(session, game.id)
                    self.last_scoring = now

                # 2. Check cleanup interval
                if now - self.last_cleanup >= config.CLEANUP_INTERVAL:
                    run_cleanup(session)
                    self.last_cleanup = now

            except Exception as e:
                logger.exception(f"Exception in Scheduler Daemon: {e}")
                if session:
                    session.rollback()
            finally:
                if session:
                    session.close()

            # Tick resolution of 1 second
            time.sleep(1)

    def stop(self):
        self.running = False
        logger.info("Scorebot Background Scheduler thread stopping.")
