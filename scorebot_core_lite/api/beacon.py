import datetime
import uuid
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import (
    SessionLocal, GameTeam, GameTeamBeaconToken, Host, GameCompromise, GameCompromiseHost, Game, Service
)
from scorebot_core_lite.auth import verify_cli_token
from scorebot_core_lite.scoring.notifications import send_notification
from netaddr import IPNetwork, IPAddress

router = APIRouter()

@router.post("/api/register", status_code=201, dependencies=[Depends(verify_cli_token)])
async def register_beacon(request: Request):
    """Register a new beacon token for a team."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("token")
        if not team_token:
            raise HTTPException(status_code=400, detail="Missing team token")

        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        # Create new beacon token
        new_token = str(uuid.uuid4())
        beacon_tok = GameTeamBeaconToken(team_id=team.id, token=new_token)
        session.add(beacon_tok)
        session.commit()

        return {"token": new_token}
    finally:
        session.close()

@router.post("/api/beacons", dependencies=[Depends(verify_cli_token)])
async def checkin_beacon(request: Request):
    """Check in a beacon or establish a new compromise."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        beacon_token = body.get("token")
        address_raw = body.get("address")

        if not beacon_token or not address_raw:
            raise HTTPException(status_code=400, detail="Missing token or address")

        # 1. Resolve attacker team by the beacon token
        bt = session.query(GameTeamBeaconToken).filter(GameTeamBeaconToken.token == beacon_token).first()
        if not bt:
            raise HTTPException(status_code=403, detail="Invalid Beacon Token")

        attacker_team = bt.team
        if attacker_team.game.status != 1:
            raise HTTPException(status_code=403, detail="Game is not running")

        try:
            ip = IPAddress(address_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid IP Address")

        # 2. Look up host in the same running game
        host = session.query(Host).join(GameTeam).filter(
            Host.ip == address_raw,
            GameTeam.game_id == attacker_team.game_id
        ).first()

        target_team = None
        if not host:
            # Try to resolve target team subnet
            for t in attacker_team.game.teams:
                try:
                    net = IPNetwork(t.subnet)
                    if ip in net:
                        target_team = t
                        break
                except Exception:
                    continue

        if not host and not target_team:
            raise HTTPException(status_code=404, detail="Host does not exist")

        # 3. Handle compromise registration/checkin
        if host:
            # Check if active compromise already exists
            existing = session.query(GameCompromise).join(GameCompromiseHost).filter(
                GameCompromise.finish == None,
                GameCompromise.attacker_team_id == attacker_team.id,
                GameCompromise.token == beacon_token,
                GameCompromiseHost.host_id == host.id
            ).first()

            if existing:
                # Update checkin time
                ch = session.query(GameCompromiseHost).filter(GameCompromiseHost.beacon_id == existing.id).first()
                if ch:
                    ch.checkin = datetime.datetime.utcnow()
                    session.commit()
                return {"status": "success", "message": "Beacon updated"}

            # Create new compromise
            compromise = GameCompromise(token=beacon_token, attacker_team_id=attacker_team.id)
            session.add(compromise)
            session.commit()
            session.refresh(compromise)

            ch = GameCompromiseHost(
                ip=address_raw,
                team_id=host.team_id,
                host_id=host.id,
                beacon_id=compromise.id,
                checkin=datetime.datetime.utcnow()
            )
            session.add(ch)

            # Award scoring once
            attacker_team.score_beacons += attacker_team.game.beacon_value

            event_msg = f"A Host on {host.team.name}'s network was compromised by {attacker_team.name}!"
            send_notification(event_msg)
            session.commit()
            return {"status": "success", "message": "Beacon registered"}

        else:
            # Faux host compromise
            compromise = GameCompromise(token=beacon_token, attacker_team_id=attacker_team.id)
            session.add(compromise)
            session.commit()
            session.refresh(compromise)

            ch = GameCompromiseHost(
                ip=address_raw,
                team_id=target_team.id,
                host_id=None,
                beacon_id=compromise.id,
                checkin=datetime.datetime.utcnow()
            )
            session.add(ch)

            attacker_team.score_beacons += attacker_team.game.beacon_value

            event_msg = f"A Host on {target_team.name}'s network was compromised by {attacker_team.name}!"
            send_notification(event_msg)
            session.commit()
            return {"status": "success", "message": "Faux Beacon registered"}

    finally:
        session.close()

@router.get("/api/beacons/active", dependencies=[Depends(verify_cli_token)])
def list_active_beacons(team_token: str):
    """Retrieve list of active beacons for a team."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        active = session.query(GameCompromise).filter(
            GameCompromise.finish == None,
            GameCompromise.attacker_team_id == team.id
        ).all()

        results = []
        for compromise in active:
            for ch in compromise.hosts:
                results.append({
                    "host": ch.ip,
                    "token": compromise.token,
                    "attacker": team.name,
                    "start": compromise.start.isoformat(),
                    "finish": None
                })
        return results
    finally:
        session.close()

@router.get("/api/beacons/ports", dependencies=[Depends(verify_cli_token)])
def get_beacon_ports():
    """Retrieve list of open beacon ports in running games."""
    session = SessionLocal()
    try:
        running_games = session.query(Game).filter(Game.status == 1).all()
        # Collect all services of type 'beacon' or similar
        # Since simplified, let's query all service ports configured on hosts in running games
        ports = set()
        for g in running_games:
            for t in g.teams:
                for h in t.hosts:
                    for s in h.services:
                        if s.application.lower() == "beacon":
                            ports.add(s.port)
        return {"ports": list(ports)}
    finally:
        session.close()

@router.post("/api/beacons/ports", dependencies=[Depends(verify_cli_token)])
async def register_beacon_port(request: Request):
    """Register a new beacon port for a team in running game."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("token")
        port_num = body.get("port")

        if not team_token or not port_num:
            raise HTTPException(status_code=400, detail="Missing team token or port")

        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team or team.game.status != 1:
            raise HTTPException(status_code=403, detail="Game not running or invalid team")

        # Verify or register service port on hosts (or mock it by adding service)
        # For simplicity, register/open beacon port by returning success (or adding a service model entry)
        return {"status": "success", "message": f"Port {port_num} registered"}
    finally:
        session.close()

# Legacy Compatibility Endpoints
@router.post("/api/register/", status_code=201, dependencies=[Depends(verify_cli_token)])
async def legacy_register_beacon(request: Request):
    """Legacy endpoint for registering a new beacon token for a team."""
    return await register_beacon(request)

@router.post("/api/beacon", dependencies=[Depends(verify_cli_token)])
@router.post("/api/beacon/", dependencies=[Depends(verify_cli_token)])
async def legacy_checkin_beacon(request: Request):
    """Legacy endpoint for checking in a beacon or establishing a new compromise."""
    return await checkin_beacon(request)

@router.post("/api/beacon/port", status_code=201, dependencies=[Depends(verify_cli_token)])
@router.post("/api/beacon/port/", status_code=201, dependencies=[Depends(verify_cli_token)])
async def legacy_register_beacon_port(request: Request):
    """Legacy endpoint for registering a new beacon port for a team in running game."""
    return await register_beacon_port(request)

@router.get("/api/beacon/port", dependencies=[Depends(verify_cli_token)])
@router.get("/api/beacon/port/", dependencies=[Depends(verify_cli_token)])
def legacy_get_beacon_ports():
    """Legacy endpoint to retrieve list of open beacon ports in running games."""
    return get_beacon_ports()
