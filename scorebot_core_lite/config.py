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

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///scorebot.db")

API_TOKEN_ADMIN = os.getenv("API_TOKEN_ADMIN", "admin-token")
API_TOKEN_MONITOR = os.getenv("API_TOKEN_MONITOR", "monitor-token")
API_TOKEN_CLI = os.getenv("API_TOKEN_CLI", "cli-token")
API_TOKEN_STORE = os.getenv("API_TOKEN_STORE", "store-token")
API_TOKEN_TICKET = os.getenv("API_TOKEN_TICKET", "ticket-token")
def _get_or_create_grey_token():
    env_token = os.getenv("API_TOKEN_GREY")
    if env_token:
        return env_token
    
    paths = [
        "/opt/scorebot/grey_token.txt",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "grey_token.txt")
    ]
    
    for path in paths:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    token = f.read().strip()
                    if token:
                        return token
            parent_dir = os.path.dirname(path)
            if os.path.exists(parent_dir) and os.access(parent_dir, os.W_OK):
                import uuid
                new_token = str(uuid.uuid4())
                with open(path, "w") as f:
                    f.write(new_token)
                return new_token
        except Exception:
            pass
            
    import uuid
    return str(uuid.uuid4())

API_TOKEN_GREY = _get_or_create_grey_token()

SCORING_INTERVAL = int(os.getenv("SCORING_INTERVAL", "300"))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "120"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")
BEACON_IP = os.getenv("BEACON_IP")
BEACON_DNS_OCTET = os.getenv("BEACON_DNS_OCTET", "68")

# Path to the game-definitions directory on disk (mounted volume or git checkout).
# The ingest module reads event/ and role/ subdirectories from this path.
GAME_DEFINITIONS_PATH = os.getenv("GAME_DEFINITIONS_PATH", "/opt/game-definitions")

# Notification Webhooks
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
GENERIC_WEBHOOK_URL = os.getenv("GENERIC_WEBHOOK_URL", "")

# X/Twitter v2 Credentials
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

def _get_static_dir():
    env_dir = os.getenv("STATIC_DIR")
    if env_dir:
        return env_dir
    
    # Check production path
    prod_path = "/opt/scorebot/current/scorebot_static"
    if os.path.exists(prod_path) and os.access(prod_path, os.W_OK):
        return prod_path
        
    # Check local repository folder (relative to this config.py file)
    local_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scorebot_static"))
    if os.path.exists(local_path) and os.access(local_path, os.W_OK):
        return local_path
        
    return "./scorebot_static"

STATIC_DIR = _get_static_dir()

def _get_media_dir():
    env_dir = os.getenv("MEDIA_DIR")
    if env_dir:
        return env_dir
    
    # 1. Check/create /opt/scorebot/scorebot_media directly (writable by www-data in prod)
    path1 = "/opt/scorebot/scorebot_media"
    if os.path.exists("/opt/scorebot") and os.access("/opt/scorebot", os.W_OK):
        try:
            os.makedirs(path1, exist_ok=True)
            return path1
        except Exception:
            pass

    # 2. Check /opt/scorebot/current/scorebot_media
    path2 = "/opt/scorebot/current/scorebot_media"
    if os.path.exists(path2) and os.access(path2, os.W_OK):
        return path2

    # 3. Check local media folder in workspace
    local_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scorebot_media"))
    try:
        os.makedirs(local_path, exist_ok=True)
        return local_path
    except Exception:
        pass
        
    return "./scorebot_media"

MEDIA_DIR = _get_media_dir()

# Proxmox VE API Integration for Zero-Footprint Guest Operations
PM_API_URL = os.getenv("PM_API_URL", "")
PM_API_USER = os.getenv("PM_API_USER", "")
PM_API_PASS = os.getenv("PM_API_PASS", "")
PM_API_TOKEN_ID = os.getenv("PM_API_TOKEN_ID", "")
PM_API_TOKEN_SECRET = os.getenv("PM_API_TOKEN_SECRET", "")



