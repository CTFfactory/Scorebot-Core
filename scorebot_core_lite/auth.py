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

import logging
from typing import Optional
from fastapi import Header, HTTPException, Depends, Request
from scorebot_core_lite import config

logger = logging.getLogger("scorebot_core_lite.auth")

def _get_token(request: Request) -> Optional[str]:
    # Check X-Scorebot-Token first (case-insensitive checking via dict/Starlette handles most, but we check explicitly too)
    for header_name in ("X-Scorebot-Token", "x-scorebot-token", "SBE-AUTH", "sbe-auth"):
        token = request.headers.get(header_name)
        if token:
            return token
    return None

async def verify_admin_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if x_scorebot_token is None or x_scorebot_token == "":
        val_type = "None" if x_scorebot_token is None else "empty string"
        headers_sent = dict(request.headers)
        redacted_headers = {k: "..." if "token" in k.lower() or "auth" in k.lower() else v for k, v in headers_sent.items()}
        logger.warning(
            "Admin auth failed from %s: X-Scorebot-Token/SBE-AUTH is %s. Received headers: %s",
            client_ip,
            val_type,
            redacted_headers
        )
        raise HTTPException(status_code=403, detail="Forbidden: Admin privilege required")
    if x_scorebot_token != config.API_TOKEN_ADMIN:
        logger.warning(
            "Admin auth failed from %s: X-Scorebot-Token mismatch (received length %d, expected length %d)",
            client_ip,
            len(x_scorebot_token),
            len(config.API_TOKEN_ADMIN)
        )
        raise HTTPException(status_code=403, detail="Forbidden: Admin privilege required")
    return x_scorebot_token

async def verify_monitor_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if not x_scorebot_token:
        logger.warning("Monitor auth failed from %s: X-Scorebot-Token / SBE-AUTH header is missing", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden: Monitor privilege required")
    if x_scorebot_token not in (config.API_TOKEN_MONITOR, config.API_TOKEN_ADMIN):
        logger.warning(
            "Monitor auth failed from %s: X-Scorebot-Token mismatch (received length %d)",
            client_ip,
            len(x_scorebot_token)
        )
        raise HTTPException(status_code=403, detail="Forbidden: Monitor privilege required")
    return x_scorebot_token

async def verify_cli_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if not x_scorebot_token:
        logger.warning("CLI auth failed from %s: X-Scorebot-Token / SBE-AUTH header is missing", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden: CLI privilege required")
    if x_scorebot_token not in (config.API_TOKEN_CLI, config.API_TOKEN_ADMIN):
        logger.warning(
            "CLI auth failed from %s: X-Scorebot-Token mismatch (received length %d)",
            client_ip,
            len(x_scorebot_token)
        )
        raise HTTPException(status_code=403, detail="Forbidden: CLI privilege required")
    return x_scorebot_token

async def verify_store_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if not x_scorebot_token:
        logger.warning("Store auth failed from %s: X-Scorebot-Token / SBE-AUTH header is missing", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden: Store privilege required")
    if x_scorebot_token not in (config.API_TOKEN_STORE, config.API_TOKEN_ADMIN):
        logger.warning(
            "Store auth failed from %s: X-Scorebot-Token mismatch (received length %d)",
            client_ip,
            len(x_scorebot_token)
        )
        raise HTTPException(status_code=403, detail="Forbidden: Store privilege required")
    return x_scorebot_token

async def verify_ticket_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if not x_scorebot_token:
        logger.warning("Ticket auth failed from %s: X-Scorebot-Token / SBE-AUTH header is missing", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden: Ticket privilege required")
    if x_scorebot_token not in (config.API_TOKEN_TICKET, config.API_TOKEN_ADMIN):
        logger.warning(
            "Ticket auth failed from %s: X-Scorebot-Token mismatch (received length %d)",
            client_ip,
            len(x_scorebot_token)
        )
        raise HTTPException(status_code=403, detail="Forbidden: Ticket privilege required")
    return x_scorebot_token

async def verify_grey_token(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    x_scorebot_token = _get_token(request)
    if not x_scorebot_token:
        logger.warning("Grey auth failed from %s: X-Scorebot-Token / SBE-AUTH header is missing", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden: Grey privilege required")
    if x_scorebot_token not in (config.API_TOKEN_GREY, config.API_TOKEN_ADMIN):
        logger.warning(
            "Grey auth failed from %s: X-Scorebot-Token mismatch (received length %d)",
            client_ip,
            len(x_scorebot_token)
        )
        raise HTTPException(status_code=403, detail="Forbidden: Grey privilege required")
    return x_scorebot_token

