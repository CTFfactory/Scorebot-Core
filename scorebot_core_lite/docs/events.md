# Scoreboard Admin Events Guide

Scorebot Core Lite includes support for triggering dynamic, real-time overlays and message alerts on the scoreboard. These are called **Scoreboard Admin Events** and are represented by the `GameEvent` model.

When an event is triggered, the scoreboard frontend receives the update via WebSockets and renders visual effects or messages to all active spectators and players.

---

## Event Types

There are four supported event types, numbered `0` to `3`:

| Type | Name | Description | Key Payload Data |
| --- | --- | --- | --- |
| `0` | **Message** | Simulates a terminal command execution at the bottom of the scoreboard page. | `command`, `text`, `response` |
| `1` | **Window** | Renders a overlay pop-up window/card on the screen. | `title`, `text`, `fullscreen` |
| `2` | **Effect** | Injects raw HTML/CSS and evaluates arbitrary JavaScript on the scoreboard. | `html` |
| `3` | **Video** | Autoplays a YouTube embed overlay (e.g. Rickroll, meme videos). | `video`, `start`, `fullscreen` |

> [!IMPORTANT]
> Because the scoreboard backend unmarshals data payloads using a `map[string]string` structure, **all values in the `"data"` payload MUST be passed as strings** (even numbers like `"0"` or booleans like `"true"`). Passing raw numbers/booleans will trigger JSON unmarshalling errors.

---

## API Endpoints

Events are triggered via a POST request to the scorebot API:

* **Endpoints:**
  * `POST /api/event/{game_id}`
  * `POST /api/event/{game_id}/`
* **Authentication Header:**
  * Requires `SBE-AUTH` or `X-Scorebot-Token` headers set to the CLI API token.
* **HTTP Response Codes:**
  * `201 Created` on success.
  * `400 Bad Request` on schema, validation, or encoding errors.
  * `403 Forbidden` if unauthorized.
  * `404 Not Found` if the game does not exist.

---

## Examples and Payloads

Here are `curl` recipes for each event type. Replace `1` with your active `game_id`, and `CLI_TOKEN` with your `API_TOKEN_CLI` value.

### Type 0: Terminal Command Message (Simulated Console)
Appends a command simulation in the terminal panel at the bottom of the scoreboard.

```bash
curl -X POST http://100.80.0.74:8000/api/event/1 \
  -H "X-Scorebot-Token: CLI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '\''{
    "data": {
      "command": "true",
      "text": "rm -rf /opt/scorebot/core",
      "response": "Permission denied\nKernel panic - not syncing: Attempted to kill init!"
    },
    "type": 0,
    "timeout": 45
  }'\''
```

### Type 1: Popup Warning / Overlay Window
Shows a popup card block in the middle of the screen.

```bash
curl -X POST http://100.80.0.74:8000/api/event/1 \
  -H "X-Scorebot-Token: CLI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '\''{
    "data": {
      "title": "ALERT: SYSTEM COMPROMISED",
      "text": "Unauthorized access detected in segment 10.64.0.0/16. Immediate purge initiated.",
      "fullscreen": "false"
    },
    "type": 1,
    "timeout": 45
  }'\''
```

### Type 2: Script / CSS Effect (Custom Injections)
Injects custom CSS stylesheets or runs custom JS code in the user'\''s browser. Excellent for styling changes, inverted colors, or matrix falling animations.

```bash
curl -X POST http://100.80.0.74:8000/api/event/1 \
  -H "X-Scorebot-Token: CLI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '\''{
    "data": {
      "html": "<style>body { background-color: #8b0000 !important; transition: all 1s ease; }</style><script>console.log(\"Scoreboard turned dark red!\");</script>"
    },
    "type": 2,
    "timeout": 60
  }'\''
```

### Type 3: Autoplay YouTube Video Overlay
Overlays a YouTube video frame over the scoreboard with autoplay enabled.

```bash
curl -X POST http://100.80.0.74:8000/api/event/1 \
  -H "X-Scorebot-Token: CLI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '\''{
    "data": {
      "video": "dQw4w9WgXcQ",
      "start": "0",
      "fullscreen": "true"
    },
    "type": 3,
    "timeout": 300
  }'\''
```

---

## Stopping Active Events

### 1. Wait for Timeout
Every event has a `"timeout"` specified in seconds. Once this time expires, the backend automatically drops the event on the next poll, and the scoreboard will close the overlay.

### 2. Manual Immediate Stop (SQL)
To stop all active screen overlays and video popups immediately, delete active records from the `game_events` database:

```bash
# PostgreSQL
sudo -u postgres psql -d scorebot -c "DELETE FROM game_events;"
```
