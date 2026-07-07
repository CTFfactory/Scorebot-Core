# Technical Reference: Scorebot Core Lite

This document compiles configuration specs, core API routing schema, and system environment variables for Scorebot Core Lite.

---

## Environment Variables

Scorebot Core Lite reads configuration settings from environment variables upon startup (defined in `config.py`).

| Variable Name | Default Value | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///scorebot.db` | SQLAlchemy connection string. Supports `sqlite:///`, `postgresql://`, `mysql://`. |
| `API_TOKEN_ADMIN` | `admin-token` | Secret header key required for all administrative modifications (`X-Scorebot-Token`). |
| `API_TOKEN_MONITOR`| `monitor-token` | Token used by health-checks and daemon services checking team uptime. |
| `API_TOKEN_CLI` | `cli-token` | Key used for command line validation scripts. |
| `API_TOKEN_STORE` | `store-token` | Key to authorize flag/inject store purchases. |
| `API_TOKEN_TICKET` | `ticket-token` | Authentication token for ticket-manager submissions. |
| `API_TOKEN_GREY` | *(Generated UUID)* | If not provided via environment, loaded from `grey_token.txt`. |
| `SCORING_INTERVAL` | `300` | The frequency in seconds at which the service checking engine runs a round. |
| `CLEANUP_INTERVAL` | `120` | Interval in seconds at which the background daemon cleans expired jobs. |
| `LOG_LEVEL` | `INFO` | Output logging granularity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `ALLOWED_ORIGINS` | `*` | CORS policy origins. Comma-separated list for strict browser filtering. |
| `BEACON_IP` | `None` | Gateway IP address where internal beacon DNS queries route. |
| `GAME_DEFINITIONS_PATH` | `/opt/game-definitions` | Disk location for fallback configuration reading. |
| `STATIC_DIR` | `./scorebot_static` | Filepath directory where custom assets are served. |
| `MEDIA_DIR` | `./scorebot_media` | Filepath directory where uploaded images and logos are stored. |

---

## API Routes & Endpoints

All authenticated requests require the token header:
`X-Scorebot-Token: <Token>`

### General / Health Endpoints
* **`GET /healthz`**: Simple healthcheck endpoint returning background daemon status.
* **`GET /metrics`**: Prometheus-formatted text metrics showing current scores, service health, and sqlite file sizes.
* **`GET /logout`**: Triggers user logout.

### Admin Endpoints
* **`POST /api/admin/games/import`**: Bulk-imports environment event config.
* **`POST /api/admin/games`**: Creates a new game instance.
* **`POST /api/admin/games/{game_id}/teams`**: Creates a team under the specified game.
* **`POST /api/admin/games/{game_id}/teams/{team_id}/hosts`**: Appends a host to a team.
* **`POST /api/admin/games/{game_id}/teams/{team_id}/hosts/{host_id}/services`**: Registers a port monitor check on a host.
* **`POST /api/admin/games/{game_id}/teams/{team_id}/hosts/{host_id}/flags`**: Plants a flag on a host.
* **`DELETE /api/admin/games/{game_id}`**: Deletes a game and cascades to clean up related teams/hosts/services.

### Gameplay Endpoints
* **`POST /api/flag`**: Flag submission portal. Expects JSON body with `flag` string and `team_token`.
* **`POST /api/hosts`**: Updates dynamically assigned IP addresses for a host FQDN.
* **`POST /api/beacon`**: Endpoint for client compromised/active beacon pings.

---

## Systemd Service Configuration

When deploying onto virtual machines, Scorebot Core Lite runs under systemd. The standard service definition template (`scorebot.service`) is shown below:

```ini
[Unit]
Description=Scorebot Core Lite FastAPI Engine
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/scorebot/current
ExecStart=/opt/scorebot/python/bin/uvicorn scorebot_core_lite.main:app --host 0.0.0.0 --port 8000
Restart=always

# Environment configuration
Environment="DATABASE_URL=sqlite:////opt/scorebot/scorebot.db"
Environment="API_TOKEN_ADMIN=secure-admin-token"
Environment="GAME_DEFINITIONS_PATH=/opt/game-definitions"
Environment="SCORING_INTERVAL=300"

[Install]
WantedBy=multi-user.target
```
