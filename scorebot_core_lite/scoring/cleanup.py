import datetime
import logging
from scorebot_core_lite.models import Game, Job, GameCompromise, GameEvent

logger = logging.getLogger("scorebot_core_lite.scoring.cleanup")

def run_cleanup(session):
    """Clean up stale jobs, expired beacons, and past events."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    logger.debug("Running background cleanup tasks...")

    # 1. Expire stale jobs
    open_jobs = session.query(Job).filter(Job.finish == None).all()
    for job in open_jobs:
        game = job.host.team.game if (job.host and job.host.team) else None
        job_timeout = game.job_timeout if game else 300
        if job.start and (now - job.start).total_seconds() > job_timeout:
            logger.info(f"Deleting stale job {job.id} after passing timeout")
            session.delete(job)

    # 2. Cleanup finished jobs
    closed_jobs = session.query(Job).filter(Job.finish != None).all()
    for job in closed_jobs:
        game = job.host.team.game if (job.host and job.host.team) else None
        job_cleanup_time = game.job_cleanup_time if game else 900
        if job.finish and (now - job.finish).total_seconds() > job_cleanup_time:
            logger.info(f"Deleting finished job {job.id} after cleanup timeout")
            session.delete(job)

    # 3. Expire inactive beacons (beacons where checkin/start timeout has passed)
    # Original scorebot: GameCompromise.is_expired() uses game.get_option("beacon_time") (default 300s).
    active_compromises = session.query(GameCompromise).filter(GameCompromise.finish == None).all()
    for compromise in active_compromises:
        game = session.query(Game).filter(Game.id == compromise.attacker.game_id).first()
        # Use per-game beacon_time if available, otherwise fall back to 300s default.
        beacon_timeout = getattr(game, "beacon_time", 300) if game else 300

        last_checkin = compromise.start
        for ch in compromise.hosts:
            if ch.checkin and ch.checkin > last_checkin:
                last_checkin = ch.checkin

        if (now - last_checkin).total_seconds() > beacon_timeout:
            logger.info(f"Closing expired compromise/beacon {compromise.id}")
            compromise.finish = now

    # 4. Remove expired GameEvents
    expired_events = session.query(GameEvent).filter(GameEvent.timeout < now).all()
    for event in expired_events:
        logger.info(f"Deleting expired event {event.id}")
        session.delete(event)

    session.commit()
