import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from scorebot_core_lite.models import SessionLocal, GameTeam, GameTicket, GameEvent
from scorebot_core_lite.auth import verify_ticket_token
from scorebot_core_lite.scoring.notifications import send_notification

router = APIRouter()

@router.post("/api/tickets", dependencies=[Depends(verify_ticket_token)])
async def submit_tickets(request: Request):
    """Submit ticket changes/creation from ticket system."""
    session = SessionLocal()
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        tickets_data = body.get("tickets")
        if not isinstance(tickets_data, list):
            raise HTTPException(status_code=400, detail="Missing or invalid tickets list")

        for td in tickets_data:
            t_id = td.get("id")
            name = td.get("name")
            details = td.get("details")
            t_type = td.get("type", "service")
            status = td.get("status", "open").lower()
            team_token = td.get("team")

            if not t_id or not name or not team_token:
                raise HTTPException(status_code=400, detail="Missing ticket ID, name or team token")

            # Look up ticket
            ticket = session.query(GameTicket).filter(GameTicket.ticket_id == int(t_id)).first()
            if not ticket:
                # Resolve team
                team = session.query(GameTeam).filter(GameTeam.token == team_token).first()
                if not team:
                    continue  # Skip invalid team

                ticket = GameTicket(
                    ticket_id=int(t_id),
                    name=name,
                    description=details or "",
                    closed=False,
                    started=datetime.datetime.utcnow(),
                    total=0,
                    team_id=team.id,
                    type=1 if t_type.lower() == "service" else 2
                )
                session.add(ticket)
                session.commit()
                session.refresh(ticket)

            # Check status transition
            target_closed = (status == "closed")
            if not ticket.closed and target_closed:
                # Close Ticket: Give back points
                ticket.closed = True
                team = ticket.team
                refund = ticket.total if ticket.type == 1 else int(ticket.total / 2)
                team.score_tickets += refund

                event_msg = f'Team {team.name} just closed a Ticket "{ticket.name}"!'
                event = GameEvent(
                    game_id=team.game_id,
                    timeout=datetime.datetime.utcnow() + datetime.timedelta(seconds=30),
                    data=f'{{"text": "{event_msg}"}}',
                    type=2
                )
                session.add(event)
                send_notification(event_msg)

            elif ticket.closed and not target_closed:
                # Reopen Ticket: Deduct points
                ticket.closed = False
                team = ticket.team
                game = team.game
                multiplier = (game.ticket_reopen_multiplier / 100.0) if game else 2.0
                deduction = int(multiplier * ticket.total)
                team.score_tickets -= deduction

                event_msg = f'Ticket "{ticket.name}" for {team.name} was reopened!'
                event = GameEvent(
                    game_id=team.game_id,
                    timeout=datetime.datetime.utcnow() + datetime.timedelta(seconds=30),
                    data=f'{{"text": "{event_msg}"}}',
                    type=2
                )
                session.add(event)
                send_notification(event_msg)

            ticket.name = name
            if details:
                ticket.description = details
            session.commit()

        return {"status": "success", "message": "Tickets processed"}
    finally:
        session.close()
