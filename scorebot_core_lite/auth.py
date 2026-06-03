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

from typing import Optional
from fastapi import Header, HTTPException, Depends
from scorebot_core_lite import config

async def verify_admin_token(x_scorebot_token: Optional[str] = Header(None, alias="X-Scorebot-Token")):
    if not x_scorebot_token or x_scorebot_token != config.API_TOKEN_ADMIN:
        raise HTTPException(status_code=403, detail="Forbidden: Admin privilege required")
    return x_scorebot_token

async def verify_monitor_token(x_scorebot_token: Optional[str] = Header(None, alias="X-Scorebot-Token")):
    if not x_scorebot_token or x_scorebot_token not in (config.API_TOKEN_MONITOR, config.API_TOKEN_ADMIN):
        raise HTTPException(status_code=403, detail="Forbidden: Monitor privilege required")
    return x_scorebot_token

async def verify_cli_token(x_scorebot_token: Optional[str] = Header(None, alias="X-Scorebot-Token")):
    if not x_scorebot_token or x_scorebot_token not in (config.API_TOKEN_CLI, config.API_TOKEN_ADMIN):
        raise HTTPException(status_code=403, detail="Forbidden: CLI privilege required")
    return x_scorebot_token

async def verify_store_token(x_scorebot_token: Optional[str] = Header(None, alias="X-Scorebot-Token")):
    if not x_scorebot_token or x_scorebot_token not in (config.API_TOKEN_STORE, config.API_TOKEN_ADMIN):
        raise HTTPException(status_code=403, detail="Forbidden: Store privilege required")
    return x_scorebot_token

async def verify_ticket_token(x_scorebot_token: Optional[str] = Header(None, alias="X-Scorebot-Token")):
    if not x_scorebot_token or x_scorebot_token not in (config.API_TOKEN_TICKET, config.API_TOKEN_ADMIN):
        raise HTTPException(status_code=403, detail="Forbidden: Ticket privilege required")
    return x_scorebot_token
