import json
import random
import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, Game, Host, Job, Service, Content
from scorebot_core_lite.auth import verify_monitor_token

router = APIRouter()

@router.get("/api/jobs", dependencies=[Depends(verify_monitor_token)])
@router.get("/api/job", dependencies=[Depends(verify_monitor_token)])
def request_job():
    """Monitor requests a host scoring job."""
    session = SessionLocal()
    try:
        # Find running games
        running_games = session.query(Game).filter(Game.status == 1).all()
        if not running_games:
            raise HTTPException(status_code=204, detail="No running games")

        # Pick a random game, team, host
        game = random.choice(running_games)
        if not game.teams:
            raise HTTPException(status_code=204, detail="No teams in running game")

        teams = list(game.teams)
        random.shuffle(teams)

        for team in teams:
            hosts = list(team.hosts)
            random.shuffle(hosts)
            for host in hosts:
                # Check if host has active/unfinished jobs
                active_job = session.query(Job).filter(Job.host_id == host.id, Job.finish == None).first()
                if active_job:
                    continue

                # Create Job
                job = Job(
                    monitor_name="monitor",
                    host_id=host.id,
                    start=datetime.datetime.utcnow()
                )
                session.add(job)
                session.commit()
                session.refresh(job)

                # Return job config
                job_json = host.get_json_job()
                job_json["id"] = job.id
                return job_json

        raise HTTPException(status_code=204, detail="No hosts available")
    finally:
        session.close()

@router.post("/api/jobs", status_code=202, dependencies=[Depends(verify_monitor_token)])
@router.post("/api/job", status_code=202, dependencies=[Depends(verify_monitor_token)])
async def submit_job(request: Request):
    """Monitor submits results of a host scoring job."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        job_id = body.get("id")
        if not job_id:
            raise HTTPException(status_code=400, detail="Missing Job ID")

        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.finish is not None:
            raise HTTPException(status_code=400, detail="Job already completed")

        host = session.query(Host).filter(Host.id == job.host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")

        # Score the Host from job results
        host_data = body.get("host", {})
        ping_sent = int(host_data.get("ping_sent", 1))
        ping_respond = int(host_data.get("ping_respond", 0))

        host.ping_last = int((ping_respond / ping_sent) * 100) if ping_sent > 0 else 0
        ping_ratio = host.ping_min if host.ping_min > 0 else (host.team.game.host_ping_ratio if host.team and host.team.game else 50)
        host.online = host.ping_last >= ping_ratio

        # Update services
        job_services = host_data.get("services", [])
        for service in host.services:
            if not host.online:
                service.status = 2  # Timeout/Offline
            else:
                # Find matching service result
                matched = False
                for js in job_services:
                    if service.port == int(js.get("port", 0)):
                        js_status = js.get("status", "timeout").lower()
                        status_map = {
                            "up": 0, "green": 0, "pass": 0,
                            "down": 1, "red": 1, "invalid": 1, "reset": 1,
                            "timeout": 2,
                            "refused": 3,
                            "yellow": 4
                        }
                        service.status = status_map.get(js_status, 2)

                        # Update content validation score
                        if "content" in js and service.content:
                            try:
                                service.content.status = int(js["content"].get("status", 0))
                            except (TypeError, ValueError):
                                service.content.status = 0
                        matched = True
                        break
                if not matched:
                    service.status = 2

        job.finish = datetime.datetime.utcnow()
        session.commit()
        return {"status": "success", "message": "Job Accepted"}
    finally:
        session.close()
