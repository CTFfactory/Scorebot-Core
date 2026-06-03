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

SCORING_INTERVAL = int(os.getenv("SCORING_INTERVAL", "15"))
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

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
