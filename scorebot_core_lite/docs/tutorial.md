# Tutorial: Get Started with Scorebot Core Lite in 10 Minutes

Welcome to the Scorebot Core Lite tutorial! This guide provides a hands-on introduction to setting up a local development instance of Scorebot Core Lite, starting the server, and simulating basic CTF scoring activity.

---

## What We Are Building
By the end of this tutorial, you will have:
1. Installed Scorebot Core Lite and its dependencies locally.
2. Started the FastAPI server and verified the background scoring scheduler is running.
3. Created a game, a blue team, and registered hosts and services.
4. Simulated a flag capture and a beacon check-in to see how the score changes.

---

## Prerequisites
Ensure you have the following installed on your machine:
* Python 3.10 or higher
* `pip` (Python package manager)
* `curl` (for interacting with the REST API)
* A terminal environment (Linux/macOS preferred)

---

## Step 1: Set Up the Environment

First, navigate to your workspace and set up a Python virtual environment. This isolates the dependencies from your global system.

```bash
# Clone the repository (if you haven't already)
git clone <repository_url> Scorebot-Core
cd Scorebot-Core

# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install the required dependencies
pip install -r scorebot_core_lite/requirements.txt
```

---

## Step 2: Configure Environment Variables

Scorebot Core Lite reads configuration from environment variables. For local testing, we can use the default SQLite database and define simple API tokens.

Create a temporary setup script or export them directly in your shell session:

```bash
export DATABASE_URL="sqlite:///scorebot.db"
export API_TOKEN_ADMIN="tutorial-admin-token"
export API_TOKEN_GREY="tutorial-grey-token"
export SCORING_INTERVAL="60"  # Set to 60 seconds for fast scoring updates
```

---

## Step 3: Run the Application

Scorebot Core Lite is powered by FastAPI and runs using `uvicorn`. Start the server with the following command:

```bash
uvicorn scorebot_core_lite.main:app --host 127.0.0.1 --port 8000 --reload
```

You should see log output indicating that the database schema was successfully initialized and the background scoring scheduler daemon has started:

```text
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:scorebot_core_lite:Initializing database schema...
INFO:scorebot_core_lite:Starting background scoring scheduler daemon...
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Open your browser and navigate to [http://127.0.0.1:8000/healthz](http://127.0.0.1:8000/healthz). You should see the health status:

```json
{
  "status": "ok",
  "service": "scorebot-core-lite",
  "scheduler_running": true
}
```

---

## Step 4: Create a Game and Team

Let's register a game and team via the API. We will use `curl` to interact with the admin API endpoints. In all administrative calls, we must supply the `X-Scorebot-Token` header.

### 1. Create a Game
Send a POST request to register a new game:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/games \
  -H "X-Scorebot-Token: tutorial-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "tutorial_game",
    "round_time": 60
  }'
```
*Response:*
```json
{
  "status": "success",
  "game_id": 1,
  "message": "Game tutorial_game created successfully."
}
```

### 2. Create a Blue Team
Register a Blue Team (e.g. "team1") for the game we just created:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/games/1/teams \
  -H "X-Scorebot-Token: tutorial-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "team1",
    "subnet": "10.1.1.0/24",
    "color": 3978094
  }'
```
*(The color `3978094` corresponds to a decimal HSL/RGB representation of blue, used by the scoreboard UI).*

---

## Step 5: Add a Scored Host and Service

Next, add a host belonging to `team1`. This host will have a scored port that the scheduler checks.

### 1. Add a Host
Register a host stub:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/games/1/teams/1/hosts \
  -H "X-Scorebot-Token: tutorial-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "fqdn": "webserver.team1.tutorial.ctf",
    "ip": "127.0.0.1"
  }'
```

### 2. Add a Service
Assign a HTTP service (port 80) to the host:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/games/1/teams/1/hosts/1/services \
  -H "X-Scorebot-Token: tutorial-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "port": 80,
    "name": "HTTP",
    "application": "http",
    "protocol": "tcp",
    "value": 50
  }'
```

---

## Step 6: Access the Admin Dashboard

Now that a game is running, you can log in to the interactive admin dashboard!

1. Open your browser and navigate to [http://127.0.0.1:8000/](http://127.0.0.1:8000/).
2. A Basic Auth prompt will appear.
3. Enter username: `admin` and password: `tutorial-admin-token` (which matches `API_TOKEN_ADMIN`).
4. You will be greeted by the admin dashboard showing your active game `tutorial_game`, registration metrics, and team states.

---

## Step 7: Simulate Scoring Activity

With the background scheduler active, we can submit flags or simulate beacon reports to see how scores are updated.

### 1. Submit a Flag Capture
First, let's look up or add a flag to our host:

```bash
# Add a flag to the host
curl -X POST http://127.0.0.1:8000/api/admin/games/1/teams/1/hosts/1/flags \
  -H "X-Scorebot-Token: tutorial-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Root Flag",
    "flag": "FLAG{w3lc0m3_t0_th3_r4ng3}",
    "value": 150,
    "description": "Found on the webserver"
  }'
```

Now, let's submit this flag as if we were an attacking team (or validating team submission) using the Flag submission endpoint:

```bash
curl -X POST http://127.0.0.1:8000/api/flag \
  -H "Content-Type: application/json" \
  -d '{
    "flag": "FLAG{w3lc0m3_t0_th3_r4ng3}",
    "team_token": "team1-token-here"
  }'
```
*(Note: You can fetch the actual team token by requesting the team details or looking at the dashboard table).*

### 2. Check metrics
Navigate to [http://127.0.0.1:8000/metrics](http://127.0.0.1:8000/metrics) in your browser or fetch it with `curl`:
```bash
curl http://127.0.0.1:8000/metrics
```
You will see Prometheus gauge metrics detailing the score, uptime, tickets, and flags of your team in real time!

---

## Next Steps
Congratulations! You have set up and verified a minimal Scorebot Core Lite environment.
* Read the [How-To Guides](how_to.md) to learn how to deploy in a production CTF environment using GitOps.
* Review the [Reference Guide](reference.md) for details about all configuration environment variables and API routes.
* Explore the [Explanation Document](explanation.md) to understand the background scheduler architecture.
