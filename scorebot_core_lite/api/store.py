from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Purchase, ScoreAudit
from scorebot_core_lite.auth import verify_store_token

router = APIRouter()

@router.get("/api/store/{team_token}/rate", dependencies=[Depends(verify_store_token)])
def get_exchange_rate(team_token: str):
    """Retrieve exchange rate for team token."""
    session = SessionLocal()
    try:
        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team or team.game.status != 1:
            raise HTTPException(status_code=404, detail="Active Team not found")
        rate = float(team.game.score_exchange_rate) / 100.0
        return {"rate": rate}
    finally:
        session.close()

@router.post("/api/store/purchase", dependencies=[Depends(verify_store_token)])
async def make_purchase(request: Request):
    """Process storefront purchase orders deducting points from team."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        team_token = body.get("team")
        order_list = body.get("order")

        if not team_token or not isinstance(order_list, list):
            raise HTTPException(status_code=400, detail="Missing team or order array")

        team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
        if not team or team.game.status != 1:
            raise HTTPException(status_code=404, detail="Active Team not found")

        for item in order_list:
            item_name = item.get("item", "Generic Item")
            price = float(item.get("price", 0.0))
            # Calculate points cost
            rate = float(team.game.score_exchange_rate) / 100.0
            points_cost = int(price * rate)

            team.score_uptime -= points_cost

            purchase = Purchase(
                item=item_name[:150],
                amount=points_cost,
                team_id=team.id
            )
            session.add(purchase)
            
            audit = ScoreAudit(
                team_id=team.id,
                source="STORE",
                amount=-points_cost,
                description=f"Purchased: {item_name[:150]}"
            )
            session.add(audit)

        session.commit()
        return {"status": "success", "message": "Purchase processed"}
    finally:
        session.close()

@router.post("/api/store/transfer", dependencies=[Depends(verify_store_token)])
async def transfer_points(request: Request):
    """Transfer points between teams or to/from Gold Team."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        source_uuid = body.get("target")
        dest_uuid = body.get("dest")
        amount = int(body.get("amount", 0))

        if amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid transfer amount")

        source_team = None
        dest_team = None

        if source_uuid:
            source_team = session.query(GameTeam).filter(GameTeam.token == source_uuid).first()
            if not source_team or source_team.game.status != 1:
                raise HTTPException(status_code=404, detail="Source team not running")

        if dest_uuid:
            dest_team = session.query(GameTeam).filter(GameTeam.token == dest_uuid).first()
            if not dest_team or dest_team.game.status != 1:
                raise HTTPException(status_code=404, detail="Destination team not running")

        if source_team:
            source_team.score_uptime -= amount
            audit_src = ScoreAudit(
                team_id=source_team.id,
                source="TRANSFER",
                amount=-amount,
                description=f"Transferred to {dest_team.name if dest_team else 'Gold Team'}"
            )
            session.add(audit_src)
        if dest_team:
            dest_team.score_uptime += amount
            audit_dest = ScoreAudit(
                team_id=dest_team.id,
                source="TRANSFER",
                amount=amount,
                description=f"Received from {source_team.name if source_team else 'Gold Team'}"
            )
            session.add(audit_dest)

        session.commit()
        return {"status": "success", "message": "Transfer processed"}
    finally:
        session.close()
