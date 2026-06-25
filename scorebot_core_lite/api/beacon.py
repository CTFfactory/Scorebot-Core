import datetime
import uuid
import socket
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import (
    SessionLocal, GameTeam, GameTeamBeaconToken, Host, GameCompromise, GameCompromiseHost, Game, Service
)
from scorebot_core_lite.auth import verify_cli_token
from scorebot_core_lite.scoring.notifications import send_notification
from netaddr import IPNetwork, IPAddress

from scorebot_core_lite import config

def resolve_dns(fqdn, dns_server):
    # Construct DNS query packet
    packet = bytearray(b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00')
    for part in fqdn.split('.'):
        packet.append(len(part))
        packet.extend(part.encode('ascii'))
    packet.append(0)
    packet.extend(b'\x00\x01')  # Type A
    packet.extend(b'\x00\x01')  # Class IN
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)
    try:
        sock.sendto(packet, (dns_server, 53))
        data, _ = sock.recvfrom(512)
        if len(data) < 12:
            return None
        ancount = int.from_bytes(data[6:8], 'big')
        if ancount == 0:
            return None
        
        idx = 12
        while data[idx] != 0:
            idx += data[idx] + 1
        idx += 5 # skip final \x00, Qtype, Qclass
        
        for _ in range(ancount):
            if idx >= len(data):
                break
            if (data[idx] & 0xc0) == 0xc0:
                idx += 2
            else:
                while data[idx] != 0:
                    idx += data[idx] + 1
                idx += 1
            atype = int.from_bytes(data[idx:idx+2], 'big')
            idx += 2
            idx += 2 # class
            idx += 4 # ttl
            rdlength = int.from_bytes(data[idx:idx+2], 'big')
            idx += 2
            if atype == 1 and rdlength == 4:
                ip_bytes = data[idx:idx+4]
                return f"{ip_bytes[0]}.{ip_bytes[1]}.{ip_bytes[2]}.{ip_bytes[3]}"
            idx += rdlength
    except Exception:
        pass
    finally:
        sock.close()
    return None

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
        if not team.offensive:
            raise HTTPException(status_code=403, detail="Team is not designated as offensive")

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
        if not attacker_team.offensive:
            raise HTTPException(status_code=403, detail="Team is not designated as offensive")
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

        if not host and target_team:
            # Try to resolve the host dynamically using DNS
            dns_ip = None
            if target_team.subnet:
                parts = target_team.subnet.split('.')
                if len(parts) >= 3:
                    dns_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.68"
            if not dns_ip and target_team.game:
                for t in target_team.game.teams:
                    if t.subnet:
                        parts = t.subnet.split('.')
                        if len(parts) >= 3:
                            dns_ip = f"{parts[0]}.{parts[1]}.{target_team.id}.68"
                            break
            if not dns_ip:
                import os
                beacon_ip = config.BEACON_IP or os.getenv("BEACON_IP")
                if beacon_ip:
                    parts = beacon_ip.split('.')
                    if len(parts) >= 3:
                        dns_ip = f"{parts[0]}.{parts[1]}.{target_team.id}.68"
            if not dns_ip:
                dns_ip = f"100.64.{target_team.id}.68"

            for h in target_team.hosts:
                resolved_ip = resolve_dns(h.fqdn, dns_ip)
                if resolved_ip == address_raw:
                    host = h
                    # Dynamically update the host's IP in the DB for cache/consistency
                    host.ip = address_raw
                    session.add(host)
                    session.commit()
                    break

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
        if not team.offensive:
            raise HTTPException(status_code=403, detail="Team is not designated as offensive")

        # Verify or register service port on hosts (or mock it by adding service)
        host = session.query(Host).join(GameTeam).filter(GameTeam.game_id == team.game_id).first()
        if not host:
            raise HTTPException(status_code=400, detail="No hosts found in the game to register the service port")

        existing_service = session.query(Service).filter(
            Service.port == port_num,
            Service.application == "beacon"
        ).first()

        if not existing_service:
            new_service = Service(
                port=port_num,
                name=f"beacon_{port_num}",
                application="beacon",
                host_id=host.id,
                value=0
            )
            session.add(new_service)
            session.commit()

        return {
            "status": "success",
            "ip": config.BEACON_IP,
            "message": f"Port {port_num} registered. Send beacons to {config.BEACON_IP}:{port_num}"
        }
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
