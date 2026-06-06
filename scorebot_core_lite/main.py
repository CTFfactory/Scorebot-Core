# Copyright (C) 2020 iDigitalFlame
# Copyright (C) 2026 luftegrof
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

from scorebot_core_lite import config
from scorebot_core_lite.models import init_db, SessionLocal, Game
from scorebot_core_lite.scoring.scheduler import SchedulerDaemon

# HTTP Basic Auth for Dashboard
security = HTTPBasic()

def verify_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, config.API_TOKEN_ADMIN or "admin")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# Routers
from scorebot_core_lite.api.scoreboard import router as scoreboard_router
from scorebot_core_lite.api.game import router as game_router
from scorebot_core_lite.api.monitor import router as monitor_router
from scorebot_core_lite.api.flag import router as flag_router
from scorebot_core_lite.api.beacon import router as beacon_router
from scorebot_core_lite.api.ticket import router as ticket_router
from scorebot_core_lite.api.store import router as store_router
from scorebot_core_lite.api.mapper import router as mapper_router
from scorebot_core_lite.api.hosts import router as hosts_router

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("scorebot_core_lite")

# Instantiate Scheduler Daemon
daemon = SchedulerDaemon()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events to start/stop the scoring scheduler and initialize DB."""
    logger.info("Initializing database schema...")
    init_db()
    logger.info("Starting background scoring scheduler daemon...")
    daemon.start()
    yield
    logger.info("Stopping background scoring scheduler daemon...")
    daemon.stop()

app = FastAPI(
    title="Scorebot-Core-Lite",
    description="Lightweight scoring engine refactored using FastAPI",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in config.ALLOWED_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Include routers
app.include_router(scoreboard_router)
app.include_router(game_router)
app.include_router(monitor_router)
app.include_router(flag_router)
app.include_router(beacon_router)
app.include_router(ticket_router)
app.include_router(store_router)
app.include_router(mapper_router)
app.include_router(hosts_router)

@app.get("/healthz", response_class=JSONResponse)
def healthz():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "scorebot-core-lite",
        "scheduler_running": daemon.running
    }

@app.get("/force-logout")
def force_logout():
    """Endpoint with basic auth challenge to clear browser cache."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )

@app.get("/logout", response_class=HTMLResponse)
def logout():
    """Logged out page."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Logged Out</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        <style>
            body {
                background-color: #0b0f19;
                color: #f3f4f6;
                font-family: 'Outfit', sans-serif;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
            }
            .card {
                background: rgba(22, 28, 45, 0.6);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 2.5rem;
                text-align: center;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            }
            h1 {
                background: linear-gradient(135deg, #60a5fa, #3b82f6);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-top: 0;
            }
            a {
                display: inline-block;
                margin-top: 1.5rem;
                background-color: #3b82f6;
                color: white;
                text-decoration: none;
                padding: 0.75rem 1.5rem;
                border-radius: 8px;
                font-weight: 600;
                transition: all 0.2s ease;
            }
            a:hover {
                box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Logged Out</h1>
            <p>You have been successfully logged out.</p>
            <a href="/">Log In Again</a>
        </div>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(verify_dashboard_auth)])
def dashboard(request: Request):
    """Admin dashboard UI."""
    session = SessionLocal()
    try:
        games = session.query(Game).all()
        logger.info(
            "Dashboard rendering. config.API_TOKEN_ADMIN length: %d, value: %s",
            len(config.API_TOKEN_ADMIN) if config.API_TOKEN_ADMIN else 0,
            "configured" if config.API_TOKEN_ADMIN else "empty"
        )
        import inspect
        sig = inspect.signature(templates.TemplateResponse)
        if "request" in sig.parameters:
            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "games": games,
                    "admin_token": config.API_TOKEN_ADMIN
                }
            )
        else:
            return templates.TemplateResponse(
                "dashboard.html",
                {
                    "request": request,
                    "games": games,
                    "admin_token": config.API_TOKEN_ADMIN
                }
            )
    finally:
        session.close()
