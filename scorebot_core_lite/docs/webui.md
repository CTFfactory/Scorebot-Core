# Admin Dashboard Web UI Reference: Scorebot Core Lite

This document describes the design, authentication requirements, functional layout, and operations available via the Scorebot Core Lite Admin Dashboard Web UI.

---

## Authentication & Access Control

Access to the Admin Dashboard (served at the root path `/`) is protected using **HTTP Basic Authentication**.

* **Username**: `admin`
* **Password**: Must match the value of the `API_TOKEN_ADMIN` environment variable (configured during deployment). If `API_TOKEN_ADMIN` is not set, it defaults to `admin`.

When accessing the dashboard, the browser caches authentication credentials. To force authorization cache clearing, administrators can visit `/force-logout` or `/logout`.

---

## Layout & Features

The dashboard is structured as a single-page administration center built with Outfit typography and a glassmorphic dark theme. It is split into three main modules:

### 1. Game Orchestration Panel
Located at the top of the interface, this panel allows admins to:
* **Create Games**: Register new game instances by entering a game name and round timer interval (in seconds).
* **Game Controls**: Start, pause, resume, or finish active games. Action buttons dynamically update the game state inside the database.
* **Scheduled Zero-Outs**: Configure a specific time at which all team scores are reset to zero. Useful for scheduling clean warm-ups or multi-stage CTF rounds.
* **Global Zero-Out**: Immediately reset all team scores and delete score adjustments for the current game.

### 2. Team & Infrastructure Tree
A nested tree view displaying registered Teams, their owned Hosts, and running Services:
* **Teams**: Shows team names, subnets, offensive status flags, and point stats.
  * **Logo Management**: Click the upload icon to set a custom team avatar (files are written to `MEDIA_DIR` and mapped on the scoreboard).
  * **Add Host**: Register a new host FQDN inside the team subnet.
* **Hosts**: Expands to reveal machines, IP bindings, online/offline ping states, and purchasable configuration states.
  * **Add Service**: Register TCP/UDP ports, target points, check script applications, and bonus toggles on a host.
* **Services**: Lists active ports, check intervals, content verification flags, and current status colors (Green, Yellow, Red).

### 3. Score Adjustments Panel
Score adjustments allow administrators to manual override points (e.g. deductions for rule infractions or bonuses for creative exploits):
* Select the target Team.
* Input the Point Delta (positive value to add points, negative value to deduct points).
* Provide a descriptive reason (logged to the `ScoreAdjustment` table and outputted to stdout via audit event listeners).

---

## Step-by-Step UI Operations

### How to Adjust a Team's Score Manually
1. Scroll down to the **Score Adjustment** panel.
2. Select the target Team from the dropdown.
3. Enter the point adjustment value (e.g., `-50` for penalty or `100` for exploit bonus).
4. Enter the justification in the **Reason** text field.
5. Click **Adjust Score**. The dashboard sends a request to `POST /api/admin/games/teams/{teamId}/adjust-score` and updates the total scores on the dashboard instantly.

### How to Upload a Team Logo
1. In the Team table, locate the team row.
2. Click the **Upload Logo** button.
3. Select an image file from your system.
4. The dashboard submits the image to `POST /api/admin/games/teams/{teamId}/logo` for server-side validation. Once passed, the file is saved inside `MEDIA_DIR` and displays on the scoreboard in real time.

#### Team Logo Image Specifications
To pass backend verification, uploaded image files must satisfy the following criteria:

* **Supported Formats / Extensions**: `png`, `jpg`, `jpeg`, `gif`, `webp`, and `svg`.
* **Maximum File Size**: Exactly **2 MB** (checked via file streams).
* **Aspect Ratio**: Must be a perfect **square (1:1 aspect ratio)**. Non-square images will be rejected.
* **Minimum Resolution**: Must be at least **300x300 pixels** (in width/height attributes or SVG viewbox).
* **Raster Processing**: Non-SVG files larger than 300x300 are automatically resized down to exactly **300x300 pixels** using Lanczos resampling and saved with optimization.
* **SVG Security Verification**: SVG uploads are parsed securely. The file will be blocked if it contains inline `<script>` tags, javascript link references (`javascript:` protocol in links), or inline HTML `on...` event handlers (protecting the scoreboard from XSS exploits).

### How to Schedule a Score Reset
1. In the Game Orchestration Panel, find the running game.
2. Select a target date/time in the datetime selector next to **Schedule Zero-Out**.
3. Click **Schedule**. The daemon schedules a job to trigger `zero_game_scores()` when the timestamp is reached.
