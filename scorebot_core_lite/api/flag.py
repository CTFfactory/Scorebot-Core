import random
import datetime
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Flag, GameEvent, ScoreAudit, Host
from scorebot_core_lite.auth import verify_cli_token, verify_admin_token
from scorebot_core_lite.scoring.notifications import send_notification

router = APIRouter()

@router.post("/api/flags", dependencies=[Depends(verify_cli_token)])
async def capture_flag(request: Request):
    """Submit a captured flag."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("token")
        flag_val = body.get("flag")

        if not team_token or not flag_val:
            raise HTTPException(status_code=400, detail="Missing token or flag")

        # Lock the attacker team row before reading game status or modifying score,
        # to prevent lost-update races with concurrent score_round ticks.
        attacker = session.query(GameTeam).filter(
            GameTeam.token == team_token
        ).with_for_update().first()
        if not attacker:
            raise HTTPException(status_code=403, detail="Invalid Team Token")

        if attacker.game.status != 1:
            raise HTTPException(status_code=403, detail="Game is not running")

        # Lock the flag row to serialise concurrent capture attempts of the same flag.
        # Any concurrent request will block here until this transaction commits, then
        # see captured_team_id is set and return "already captured".
        flag = session.query(Flag).filter(
            Flag.flag == flag_val,
            Flag.enabled == True
        ).with_for_update().first()

        if not flag or flag.team_id == attacker.id or flag.team.game_id != attacker.game_id:
            raise HTTPException(status_code=404, detail="Flag not valid")

        # 3. Check if already captured
        if flag.captured_team_id is not None:
            return {"status": "success", "message": "Flag already captured"}

        # 4. Lock the victim team row before modifying their score.
        victim = session.query(GameTeam).filter(
            GameTeam.id == flag.team_id
        ).with_for_update().first()

        # 5. Perform capture
        flag.captured_team_id = attacker.id

        # Deduct/Award points based on flag_stolen_rate setting
        game = attacker.game
        if game.flag_stolen_rate > 0:
            stolen_amt = game.flag_stolen_rate
            victim.score_flags -= stolen_amt
            session.add(ScoreAudit(
                team_id=victim.id,
                source="FLAG-STOLEN",
                amount=-stolen_amt,
                description=f"Flag stolen by {attacker.name}"
            ))
        else:
            stolen_amt = flag.value * game.flag_captured_multiplier
            victim.score_flags -= stolen_amt
            session.add(ScoreAudit(
                team_id=victim.id,
                source="FLAG-STOLEN",
                amount=-stolen_amt,
                description=f"Flag stolen by {attacker.name}"
            ))

            # Add to attacker
            capture_amt = flag.value * game.flag_captured_multiplier
            attacker.score_flags += capture_amt
            session.add(ScoreAudit(
                team_id=attacker.id,
                source="FLAG-CAPTURE",
                amount=capture_amt,
                description=f"Captured flag from {victim.name}"
            ))

        # Add Event
        event_msg = f"A Flag from {victim.name} was stolen by {attacker.name}!"
        # Create event record
        event = GameEvent(
            game_id=game.id,
            timeout=datetime.datetime.utcnow() + datetime.timedelta(seconds=30),
            data=f'{{"text": "{event_msg}"}}',
            type=1
        )
        session.add(event)
        session.commit()

        # Send Pluggable Notifications
        send_notification(event_msg)

        # 5. Hint response (optional description of another uncaptured flag from the same victim)
        victim_flags = session.query(Flag).filter(
            Flag.team_id == victim.id,
            Flag.enabled == True,
            Flag.captured_team_id == None
        ).all()

        if victim_flags:
            hint_flag = random.choice(victim_flags)
            return {"message": hint_flag.description}

        return {"status": "success", "message": "Flag captured"}
    finally:
        session.close()

# Legacy Compatibility Endpoints
@router.post("/api/flag", dependencies=[Depends(verify_cli_token)])
@router.post("/api/flag/", dependencies=[Depends(verify_cli_token)])
async def legacy_capture_flag(request: Request):
    """Legacy endpoint for submitting a captured flag."""
    return await capture_flag(request)


class AdminFlagCreateSchema(BaseModel):
    name: str
    flag: str
    value: int = 100
    description: str = ""


@router.post("/api/admin/games/{game_id}/hosts/{host_id}/flags", dependencies=[Depends(verify_admin_token)])
def register_flag(game_id: int, host_id: int, data: AdminFlagCreateSchema):
    """Admin endpoint to dynamically register a generated flag for a host."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        if not host.team or host.team.game_id != game_id:
            raise HTTPException(status_code=400, detail="Host team does not belong to the specified game")

        # Check if flag string already exists to avoid duplicates
        existing = session.query(Flag).filter(Flag.flag == data.flag).first()
        if existing:
            return {
                "status": "success",
                "flag_id": existing.id,
                "message": "Flag already registered"
            }

        # Check if a flag with the same name on the same host already exists
        existing_by_name = session.query(Flag).filter(
            Flag.host_id == host_id,
            Flag.name == data.name
        ).first()

        if existing_by_name:
            existing_by_name.flag = data.flag
            existing_by_name.description = data.description
            existing_by_name.value = data.value
            existing_by_name.enabled = True
            existing_by_name.captured_team_id = None
            session.commit()
            session.refresh(existing_by_name)
            return {
                "status": "success",
                "flag_id": existing_by_name.id,
                "message": f"Flag '{data.name}' updated successfully for host {host.fqdn}"
            }

        flag = Flag(
            name=data.name,
            flag=data.flag,
            enabled=True,
            description=data.description,
            value=data.value,
            host_id=host_id,
            team_id=host.team_id
        )
        session.add(flag)
        session.commit()
        session.refresh(flag)
        return {
            "status": "success",
            "flag_id": flag.id,
            "message": f"Flag '{data.name}' registered successfully for host {host.fqdn}"
        }
    finally:
        session.close()


@router.get("/api/admin/games/{game_id}/flags", dependencies=[Depends(verify_admin_token)])
def get_game_flags(game_id: int):
    """Admin endpoint to retrieve all registered flags for a game."""
    session = SessionLocal()
    try:
        flags = session.query(Flag).join(Host).join(GameTeam).filter(GameTeam.game_id == game_id).all()
        return [
            {
                "id": f.id,
                "name": f.name,
                "flag": f.flag,
                "enabled": f.enabled,
                "description": f.description,
                "value": f.value,
                "host_id": f.host_id,
                "host_name": f.host.name if f.host else None,
                "host_fqdn": f.host.fqdn if f.host else None,
                "team_id": f.team_id,
                "team_name": f.team.name if f.team else None,
                "captured_team_id": f.captured_team_id,
            }
            for f in flags
        ]
    finally:
        session.close()


@router.get("/api/admin/games/{game_id}/hosts/{host_id}/flags", dependencies=[Depends(verify_admin_token)])
def get_host_flags(game_id: int, host_id: int):
    """Admin endpoint to retrieve registered flags for a specific host."""
    session = SessionLocal()
    try:
        host = session.query(Host).filter(Host.id == host_id).first()
        if not host:
            raise HTTPException(status_code=404, detail="Host not found")
        if not host.team or host.team.game_id != game_id:
            raise HTTPException(status_code=400, detail="Host team does not belong to the specified game")

        flags = session.query(Flag).filter(Flag.host_id == host_id).all()
        return [
            {
                "id": f.id,
                "name": f.name,
                "flag": f.flag,
                "enabled": f.enabled,
                "description": f.description,
                "value": f.value,
                "host_id": f.host_id,
                "team_id": f.team_id,
                "captured_team_id": f.captured_team_id,
            }
            for f in flags
        ]
    finally:
        session.close()


@router.post("/api/admin/games/{game_id}/flags/verify", dependencies=[Depends(verify_admin_token)])
@router.get("/api/admin/games/{game_id}/flags/verify", dependencies=[Depends(verify_admin_token)])
def verify_game_flags(game_id: int):
    """Admin endpoint to verify flag presence and content for all hosts in a game via Proxmox QEMU Guest Agent API."""
    import base64
    import os
    import json
    import logging
    import ssl
    import urllib.request
    import urllib.parse
    from scorebot_core_lite import config

    logger = logging.getLogger("scorebot_core_lite.api.flag")
    session = SessionLocal()
    try:
        flags = session.query(Flag).join(Host).join(GameTeam).filter(GameTeam.game_id == game_id).all()
        if not flags:
            return {"status": "success", "message": "No registered flags found for game", "total_flags": 0, "results": []}

        pm_url = config.PM_API_URL.rstrip("/")
        pm_token_id = config.PM_API_TOKEN_ID
        pm_token_secret = config.PM_API_TOKEN_SECRET

        if not pm_url or not pm_token_id or not pm_token_secret:
            return {
                "status": "warning",
                "message": "Proxmox API credentials not configured in Scorebot (PM_API_URL, PM_API_TOKEN_ID, PM_API_TOKEN_SECRET)",
                "total_flags": len(flags),
                "ok": 0,
                "missing": 0,
                "tampered": 0,
                "unreachable": len(flags),
                "results": [
                    {
                        "flag_id": f.id,
                        "flag_name": f.name,
                        "host_fqdn": f.host.fqdn if f.host else "",
                        "team_name": f.team.name if f.team else "",
                        "status": "UNCHECKED (Proxmox API credentials missing)",
                        "method": "None"
                    }
                    for f in flags
                ]
            }

        headers = {"Authorization": f"PVEAPIToken={pm_token_id}={pm_token_secret}"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        cluster_map = {}
        try:
            req = urllib.request.Request(f"{pm_url}/cluster/resources?type=vm", headers=headers)
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            with opener.open(req, timeout=10) as r:
                data = json.loads(r.read().decode('utf-8')).get("data", [])
                for vm in data:
                    v_name = vm.get("name", "").lower()
                    v_node = vm.get("node")
                    v_id = str(vm.get("vmid", ""))
                    if v_name and v_node and v_id:
                        cluster_map[v_name] = (v_node, v_id)
                        short_vname = v_name.split(".")[0]
                        if short_vname not in cluster_map:
                            cluster_map[short_vname] = (v_node, v_id)
        except Exception as e:
            logger.error(f"Error querying Proxmox cluster resources: {e}")

        role_flag_paths = {}
        if os.path.exists(config.GAME_DEFINITIONS_PATH):
            try:
                gdefs_file = os.path.join(config.GAME_DEFINITIONS_PATH, "game_definitions.json")
                if not os.path.exists(gdefs_file):
                    gdefs_file = "game_definitions.json"
                if os.path.exists(gdefs_file):
                    with open(gdefs_file, "r") as gf:
                        gdefs = json.load(gf)
                        for r in gdefs.get("role", []):
                            r_name = r.get("role", {}).get("name")
                            for fl in r.get("role", {}).get("flags", []):
                                role_flag_paths[(r_name, fl.get("name"))] = fl.get("path")
            except Exception:
                pass

        results = []
        ok_cnt, missing_cnt, tampered_cnt, unreachable_cnt = 0, 0, 0, 0

        for f in flags:
            host_fqdn = f.host.fqdn if f.host else ""
            host_name = f.host.name if f.host else ""
            team_name = f.team.name if f.team else ""

            node = f.host.node if (f.host and f.host.node) else None
            vmid = f.host.vmid if (f.host and f.host.vmid) else None

            if not node or not vmid:
                pve_tuple = cluster_map.get(host_fqdn.lower()) or cluster_map.get(host_name.lower())
                if pve_tuple:
                    node, vmid = pve_tuple

            path = f.description if (f.description and (f.description.startswith("/") or ":\\" in f.description or "C:\\" in f.description)) else None
            if not path:
                for (r_name, fl_name), p in role_flag_paths.items():
                    if fl_name == f.name:
                        path = p
                        break

            if not node or not vmid or not path:
                unreachable_cnt += 1
                results.append({
                    "flag_id": f.id,
                    "flag_name": f.name,
                    "host_fqdn": host_fqdn,
                    "team_name": team_name,
                    "status": "UNREACHABLE (Missing Proxmox VMID or path)",
                    "method": "PVE-Agent"
                })
                continue

            enc_path = urllib.parse.quote(path, safe='')
            agent_url = f"{pm_url}/nodes/{node}/qemu/{vmid}/agent/file-read?file={enc_path}"
            status = "UNREACHABLE"

            try:
                areq = urllib.request.Request(agent_url, headers=headers)
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
                with opener.open(areq, timeout=10) as ar:
                    adata = json.loads(ar.read().decode('utf-8')).get("data", {})
                    b64_content = adata.get("content", "")
                    content_str = base64.b64decode(b64_content).decode("utf-8", errors="ignore")
                    if f.flag in content_str:
                        status = "OK"
                    else:
                        status = "TAMPERED"
            except urllib.error.HTTPError as he:
                err_text = str(he.read().decode('utf-8', errors='ignore'))
                if "No such file" in err_text or "NotFound" in err_text or "file not found" in err_text.lower():
                    status = "MISSING"
                else:
                    status = "MISSING"
            except Exception:
                status = "UNREACHABLE"

            if status == "OK":
                ok_cnt += 1
            elif status == "MISSING":
                missing_cnt += 1
            elif status == "TAMPERED":
                tampered_cnt += 1
            else:
                unreachable_cnt += 1

            results.append({
                "flag_id": f.id,
                "flag_name": f.name,
                "host_fqdn": host_fqdn,
                "team_name": team_name,
                "status": status,
                "method": "PVE-Agent"
            })

        overall_status = "success" if (missing_cnt == 0 and tampered_cnt == 0) else "warning"
        return {
            "status": overall_status,
            "message": f"Flag audit complete: {ok_cnt} OK, {missing_cnt} MISSING, {tampered_cnt} TAMPERED, {unreachable_cnt} UNREACHABLE",
            "total_flags": len(flags),
            "ok": ok_cnt,
            "missing": missing_cnt,
            "tampered": tampered_cnt,
            "unreachable": unreachable_cnt,
            "results": results
        }
    finally:
        session.close()



