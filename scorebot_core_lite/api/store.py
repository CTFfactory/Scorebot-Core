from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, Purchase, ScoreAudit, Game, StorePriceOverride
from scorebot_core_lite.auth import verify_store_token, verify_grey_token

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

        team = session.query(GameTeam).filter(
            GameTeam.token == team_token
        ).with_for_update().first()
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

        # Lock both team rows in ascending PK order to prevent deadlocks when two
        # concurrent transfers involve the same pair of teams in opposite directions.
        teams_to_lock = sorted(
            [t for t in [source_team, dest_team] if t],
            key=lambda t: t.id
        )
        for t in teams_to_lock:
            session.query(GameTeam).filter(GameTeam.id == t.id).with_for_update().first()

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

@router.get("/api/store/prices", dependencies=[Depends(verify_store_token)])
def get_store_prices():
    """Retrieve active store price overrides."""
    session = SessionLocal()
    try:
        # Find active game
        game = session.query(Game).filter(Game.status == 1).first()
        if not game:
            game = session.query(Game).first()
        if not game:
            return {}
        
        overrides = session.query(StorePriceOverride).filter(StorePriceOverride.game_id == game.id).all()
        return {ov.item_id: ov.price for ov in overrides}
    finally:
        session.close()

@router.patch("/api/admin/store/prices", dependencies=[Depends(verify_grey_token)])
async def update_store_prices(request: Request):
    """Update active store price overrides."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        prices = body.get("prices")
        if not isinstance(prices, dict):
            raise HTTPException(status_code=400, detail="Expected 'prices' dict")
            
        # Find active game
        game = session.query(Game).filter(Game.status == 1).first()
        if not game:
            game = session.query(Game).first()
        if not game:
            raise HTTPException(status_code=404, detail="No active game found")
            
        for item_id, price in prices.items():
            if price is None:
                session.query(StorePriceOverride).filter(
                    StorePriceOverride.game_id == game.id,
                    StorePriceOverride.item_id == item_id
                ).delete()
            else:
                try:
                    price_val = float(price)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid price value for {item_id}")
                
                override = session.query(StorePriceOverride).filter(
                    StorePriceOverride.game_id == game.id,
                    StorePriceOverride.item_id == item_id
                ).first()
                if override:
                    override.price = price_val
                else:
                    override = StorePriceOverride(game_id=game.id, item_id=item_id, price=price_val)
                    session.add(override)
                    
        session.commit()
        return {"status": "success", "message": "Prices updated"}
    finally:
        session.close()

