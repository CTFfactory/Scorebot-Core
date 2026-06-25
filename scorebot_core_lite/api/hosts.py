from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Host, Service
from scorebot_core_lite.auth import verify_cli_token

router = APIRouter()

@router.post("/api/hosts", dependencies=[Depends(verify_cli_token)])
async def register_host(request: Request):
    """Registers or updates a scored host with services (called by CTFfactory)."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("team")
        ip = body.get("ip")
        dns = body.get("dns")

        if not team_token or ip is None or not dns:
            raise HTTPException(status_code=400, detail="Missing team, ip or dns fields")

        # Find team
        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        # Find or create host by FQDN
        host_short_name = dns.split(".")[0] if "." in dns else dns
        host = session.query(Host).filter(Host.fqdn == dns).first()
        if not host:
            host = Host(fqdn=dns, ip=ip, name=host_short_name, team_id=team.id, online=False)
            session.add(host)
            session.commit()
            session.refresh(host)
        else:
            host.ip = ip
            host.name = host_short_name
            host.team_id = team.id

        # Register services
        services_list = body.get("services", [])
        # Remove old services except beacon to prevent duplicates
        session.query(Service).filter(Service.host_id == host.id, Service.application != "beacon").delete()

        for svc in services_list:
            port = int(svc.get("port", 80))
            name = svc.get("name", f"port_{port}")
            bonus = bool(svc.get("bonus", False))
            value = int(svc.get("value", 50))
            app = svc.get("application", "ping")
            proto_str = svc.get("protocol", "tcp").lower()
            proto = 2 if proto_str == "udp" else 1

            service = Service(
                port=port,
                name=name[:64],
                bonus=bonus,
                value=value,
                host_id=host.id,
                application=app[:64],
                protocol=proto,
                status=2  # default offline timeout status
            )
            session.add(service)

        session.commit()
        return {"id": host.id, "status": "success", "message": "Host registered"}
    finally:
        session.close()

@router.delete("/api/hosts/{host_id}", dependencies=[Depends(verify_cli_token)])
def deregister_host(host_id: int):
    """Decommissions a host by mapping its team to None, stopping scoring."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")

        # Django behavior: set team to None
        host.team_id = None
        session.commit()
        return {"status": "success", "message": "Host deregistered"}
    finally:
        session.close()

@router.delete("/api/hosts", dependencies=[Depends(verify_cli_token)])
def deregister_host_by_fqdn(fqdn: str):
    """Decommissions a host by mapping its team to None, stopping scoring, looking up by FQDN."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.fqdn == fqdn).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")

        host.team_id = None
        session.commit()
        return {"status": "success", "message": "Host deregistered"}
    finally:
        session.close()

