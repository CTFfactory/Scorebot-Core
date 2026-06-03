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
                # 1. Check scoring interval
                if now - self.last_scoring >= config.SCORING_INTERVAL:
                    session = SessionLocal()
                    running_games = session.query(Game).filter(Game.status == 1).all()
                    for game in running_games:
                        score_round(session, game.id)
                    self.last_scoring = now

                # 2. Check cleanup interval
                if now - self.last_cleanup >= config.CLEANUP_INTERVAL:
                    if not session:
                        session = SessionLocal()
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
