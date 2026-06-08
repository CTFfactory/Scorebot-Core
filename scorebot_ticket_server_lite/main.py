import os
import requests
import inspect
import secrets
import base64
from fastapi import FastAPI, Request, Depends, Form, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from scorebot_ticket_server_lite.database import get_db, Base, engine
from scorebot_ticket_server_lite import models

# Initialize Database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Scorebot Ticket Server Lite")

# Templates directory setup
# Create templates directory if not exists
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE_DIR, "templates", "partials"), exist_ok=True)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SCOREBOT_CORE_URL = os.getenv("SCOREBOT_CORE_URL", "http://localhost:8000")
TICKET_TOKEN = os.getenv("API_TOKEN_TICKET", "ticket-token")

try:
    from scorebot_core_lite import config
    GREY_TOKEN = config.API_TOKEN_GREY
    ADMIN_PASS = config.API_TOKEN_ADMIN
except ImportError:
    GREY_TOKEN = os.getenv("API_TOKEN_GREY", "grey-token")
    ADMIN_PASS = os.getenv("API_TOKEN_ADMIN", "admin-token")

security = HTTPBasic()

def verify_admin_auth(request: Request):
    """Authenticate Grey Team admins using HTTP Basic Auth, query token, X-Scorebot-Token header, or cookie."""

    # 1. Check Query Parameter
    token_param = request.query_params.get("token")
    if token_param == GREY_TOKEN:
        return True

    # 2. Check Header
    token_header = request.headers.get("X-Scorebot-Token") or request.headers.get("SBE-AUTH")
    if token_header == GREY_TOKEN:
        return True

    # 3. Check Cookie
    token_cookie = request.cookies.get("grey_team_token")
    if token_cookie == GREY_TOKEN:
        return True

    # 4. Fallback to HTTP Basic Auth
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, password = decoded.split(":", 1)
            if secrets.compare_digest(username, "admin") and (
                secrets.compare_digest(password, ADMIN_PASS) or
                secrets.compare_digest(password, GREY_TOKEN)
            ):
                return True
        except Exception:
            pass

    # If all fail, raise 401
    raise HTTPException(
        status_code=401,
        detail="Unauthorized access",
        headers={"WWW-Authenticate": "Basic"},
    )

def render_template(request: Request, name: str, context: dict):
    """Render a template, checking the Starlette/FastAPI TemplateResponse signature compatibility."""
    sig = inspect.signature(templates.TemplateResponse)
    if "request" in sig.parameters:
        return templates.TemplateResponse(
            request=request,
            name=name,
            context=context
        )
    else:
        # Fallback for older Starlette versions
        full_context = {"request": request}
        full_context.update(context)
        return templates.TemplateResponse(name, full_context)

def sync_ticket_to_core(ticket: models.Ticket):
    """Sync ticket status to scorebot core lite API."""
    url = f"{SCOREBOT_CORE_URL}/api/tickets"
    headers = {
        "X-Scorebot-Token": TICKET_TOKEN,
        "Content-Type": "application/json"
    }
    # Translate status/category as expected by core
    payload = {
        "tickets": [
            {
                "id": ticket.ticket_id,
                "name": ticket.title,
                "details": ticket.description or "",
                "team": ticket.team_token,
                "type": ticket.category,
                "status": ticket.status
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error syncing ticket to core: {e}")
        return False

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return RedirectResponse(url="/tickets/team")

# --- BLUE TEAM PORTAL ---

@app.get("/team", response_class=HTMLResponse)
async def team_portal(request: Request, token: str = None, db: Session = Depends(get_db)):
    if not token:
        # Check for cookie
        token = request.cookies.get("team_token")
    
    if not token:
        return render_template(request, "login.html", {"error": None})
    
    # Get tickets for this team token
    tickets_list = db.query(models.Ticket).filter(models.Ticket.team_token == token).all()
    
    response = render_template(request, "team.html", {
        "token": token,
        "tickets": tickets_list
    })
    response.set_cookie(key="team_token", value=token)
    return response

@app.post("/team/login")
async def team_login(request: Request, token: str = Form(...)):
    # Redirect to portal with token
    response = RedirectResponse(url=f"/tickets/team?token={token}", status_code=303)
    response.set_cookie(key="team_token", value=token)
    return response

@app.get("/team/logout")
async def team_logout():
    response = RedirectResponse(url="/tickets/team", status_code=303)
    response.delete_cookie("team_token")
    return response

# --- ADMIN / GREY TEAM PORTAL ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_portal(request: Request, db: Session = Depends(get_db)):
    verify_admin_auth(request)
    tickets_list = db.query(models.Ticket).order_by(models.Ticket.created_at.desc()).all()
    
    response = render_template(request, "admin.html", {
        "tickets": tickets_list
    })
    
    # If a token query param was used, save it as a cookie to authorize subsequent HTMX requests
    token_param = request.query_params.get("token")
    if token_param == GREY_TOKEN:
        response.set_cookie(key="grey_team_token", value=token_param)
        
    return response

@app.post("/admin/tickets")
async def create_ticket(
    request: Request,
    ticket_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(None),
    category: int = Form(0),
    team_token: str = Form(...),
    team_name: str = Form(None),
    db: Session = Depends(get_db)
):
    verify_admin_auth(request)
    
    # Check if ticket ID already exists
    existing = db.query(models.Ticket).filter(models.Ticket.ticket_id == ticket_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ticket ID already exists")

    ticket = models.Ticket(
        ticket_id=ticket_id,
        title=title,
        description=description,
        category=category,
        team_token=team_token,
        team_name=team_name or "Unknown Team",
        status="open"
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    
    # Sync to scorebot core
    sync_ticket_to_core(ticket)
    
    # Redirect or refresh via HTMX
    if request.headers.get("hx-request"):
        tickets_list = db.query(models.Ticket).order_by(models.Ticket.created_at.desc()).all()
        return render_template(request, "partials/ticket_rows.html", {
            "tickets": tickets_list
        })
        
    return RedirectResponse(url="/tickets/admin", status_code=303)

@app.post("/admin/tickets/{t_id}/toggle")
async def toggle_ticket(t_id: int, request: Request, db: Session = Depends(get_db)):
    verify_admin_auth(request)
    
    ticket = db.query(models.Ticket).filter(models.Ticket.id == t_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
        
    # Toggle status
    ticket.status = "closed" if ticket.status == "open" else "open"
    db.commit()
    db.refresh(ticket)
    
    # Sync status change to Core
    sync_ticket_to_core(ticket)
    
    if request.headers.get("hx-request"):
        return render_template(request, "partials/ticket_status.html", {
            "ticket": ticket
        })
        
    return RedirectResponse(url="/tickets/admin", status_code=303)

# --- MESSAGING AND DISCUSSION ---

@app.get("/tickets/{t_id}/messages", response_class=HTMLResponse)
async def get_messages(t_id: int, request: Request, sender_role: str = "blue_team", db: Session = Depends(get_db)):
    ticket = db.query(models.Ticket).filter(models.Ticket.id == t_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return render_template(request, "partials/messages.html", {
        "ticket": ticket,
        "sender_role": sender_role
    })

@app.post("/tickets/{t_id}/messages")
async def send_message(
    t_id: int,
    request: Request,
    message: str = Form(...),
    sender_role: str = Form(...), # "blue_team" or "grey_team"
    db: Session = Depends(get_db)
):
    ticket = db.query(models.Ticket).filter(models.Ticket.id == t_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
        
    new_msg = models.TicketMessage(
        ticket_id=ticket.id,
        sender=sender_role,
        message=message
    )
    db.add(new_msg)
    db.commit()
    db.refresh(ticket)
    
    if request.headers.get("hx-request"):
        return render_template(request, "partials/messages.html", {
            "ticket": ticket,
            "sender_role": sender_role
        })
        
    target_redirect = "/tickets/admin" if sender_role == "grey_team" else "/tickets/team"
    return RedirectResponse(url=target_redirect, status_code=303)
