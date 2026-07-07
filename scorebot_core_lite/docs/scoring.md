# Scoring Mechanisms: Scorebot Core Lite

This document details the scoring algorithms, tick calculations, flag capture points, and penalization structures implemented in Scorebot Core Lite.

---

## Scoring Architecture Overview

During a running game, scores are divided into four primary buckets:
1. **Uptime (Service Status checks)**
2. **Beacons (Active system compromises)**
3. **Tickets (Gray/Gold Team Incident / Helpdesk logs)**
4. **Flags (Captured security strings)**

Total score is evaluated dynamically:
$$\text{Total Score} = \text{Score}_{\text{Flags}} + \text{Score}_{\text{Uptime}} + \text{Score}_{\text{Tickets}} + \text{Score}_{\text{Beacons}} + \sum \text{Adjustments}$$

---

## Team Classifications: Offensive vs. Defensive

Scorebot divides competition roles by designating teams as either **Offensive** (`offensive = True`) or **Defensive** (`offensive = False`). This designation restricts API functions and controls scoring structures:

### 1. Offensive Teams (Red Team / Attackers)
Offensive teams do not own scored hosts or service checks. Instead, they interact with the range by exploiting targets and registering compromises:
* **API Access Controls**:
  * **Beacon Tokens**: Only offensive teams can request and register beacon tokens via `POST /api/register`.
  * **Compromise Check-ins**: Only offensive teams can submit heartbeats/compomises via `POST /api/beacons` or open target listening ports via `POST /api/beacons/ports`.
* **Scoring Rules**:
  * Earn one-time bonuses (`beacon_value`) upon registering a new compromise on a host.
  * Earn points when capturing flags:
    $$\text{Attacker Flags Score} = \text{Attacker Flags Score} + (\text{flag.value} \times \text{game.flag\_captured\_multiplier})$$

### 2. Defensive Teams (Blue Teams / Defenders)
Defensive teams are the owners of the infrastructure targets under check:
* **API Access Controls**:
  * Barred from registering beacon tokens or posting compromises.
* **Scoring Rules**:
  * Earn points continuously via **Uptime Scoring** (checks on their registered host services).
  * Lose points dynamically when services go down.
  * Lose points on an ongoing basis for every scoring tick that an active Red Team compromise remains on their hosts (deducting `beacon_value` per round).
  * Lose points for open administrative support/incident tickets past the grace period.
  * Lose points when their flags are stolen.

### 3. Hybrid / Transition State (Offensive Blue Teams)
If a Blue Team's status is changed from defensive to offensive (`offensive = True`):
* **Retained Defensive Assets**: They retain all their assigned host stubs and running services, and the scoring loop **continues to calculate and award them uptime points** for those services.
* **Acquired Offensive Powers**: They gain the ability to call the beacon registration APIs (`POST /api/register` and `POST /api/beacons`), allowing them to deploy beacons and register compromises against other teams while maintaining their own defensive infrastructure.

---

## 1. Uptime Scoring (Service Checks)

Every round tick, the `SchedulerDaemon` computes service uptime scores. Although the scoring database schema and application code support grouping multiple duplicate services under "Application Groups" (taking the maximum scoring check), **in practice, each host is deployed with exactly one service and port per application type** (e.g. exactly one HTTP service on port 80, one SSH service on port 22, etc.).

### Uptime Calculation Flow
1. **Host Offline Check**: If a host is offline (`online = False`), no services on that host are scored for the round.
2. **Direct Service Check**: For each service on the host:
   * **Active (`status = 0`)**:
     * If the service has a `content` record (verifying body text correctness), the points are scaled by the status percentage:
       $$\text{Points} = \lfloor \text{service.value} \times (\text{content.status} / 100) \rfloor$$
     * If there is no content check, the team receives the full `service.value`.
   * **Warning / Yellow (`status = 4`)**:
     * The service receives exactly 50% of the value:
       $$\text{Points} = \lfloor \text{service.value} \times 0.5 \rfloor$$
   * **Down / Timeout / Refused / Inactive Bonus**:
     * Receives $0$ points.
3. **Cumulative Sum**: The scores from all services on the host are added together to build the host uptime total:
   $$\text{Host Uptime Score} = \sum \text{Service Points}$$

---

## 2. Beacon Scoring (Red Team Compromises)

Beacons capture system compromises. They score points differently depending on whether they are one-time events or continuous loops:

* **Attacker (One-time Reward)**: When a beacon is registered via `POST /api/beacon` for the first time, the attacker team is awarded a flat bonus equal to `game.beacon_value`. They do NOT continue to receive points round-after-round.
* **Victim (Per-Round Penalty)**: For every round that the compromise remains active (`compromise.finish = None`), the victim team's score is penalized:
  $$\text{Victim Points Adjustment} = - \text{game.beacon\_value}$$
* **Beacon Expiration**: The background cleanup thread polls compromises. If a beacon has not updated its check-in timestamp (`ch.checkin`) for more than `game.beacon_time` seconds (defaults to 300s), the engine marks it finished (`compromise.finish = now`).
* **Re-Compromise Award**: Once a compromise is marked finished (either via timeout expiration or administrator intervention), the host's active compromise count drops to zero. Any subsequent new beacon registration check-in from that host will create a brand new compromise, netting the Red Team another one-time point reward.
* **Port Agnosticism**: The scoring engine identifies compromises solely by the target host IP and the beacon token. The source/destination port used by the beacon server does not isolate or duplicate compromises.

---

## 3. Incident Ticket Scoring

Ticketing is managed by competition administrators. Members of the **Gray Team** or **Gold Team** open support, administrative, or incident tickets targeting specific **Blue Teams**. Unresolved tickets penalize the targeted Blue Team once the grace period expires:

* **Grace Period**: No points are deducted if the ticket has been open for less than `game.ticket_grace_period` (in seconds).
* **Penalty Loop**: After the grace period passes, the ticket deducts points at the rate of `game.ticket_cost` per round:
  $$\text{Team Ticket Score} = \text{Team Ticket Score} - \text{game.ticket\_cost}$$
* **Upper Limit**: The deductions accrue until the total deduction reaches `game.ticket_max_score` or the ticket has been open longer than `game.ticket_max_scoring` seconds.

---

## 4. Flag Captures (Stealing Flags)

Flag capture runs via `POST /api/flag`. When an attacker submits a valid flag planted on a victim host:

1. **Attacker Reward**: The attacker team receives points scaled by a game multiplier:
   $$\text{Attacker Flags Score} = \text{Attacker Flags Score} + (\text{flag.value} \times \text{game.flag\_captured\_multiplier})$$
2. **Victim Penalty**: The victim team loses points. The penalty amount depends on the game config:
   * If `game.flag_stolen_rate` is greater than $0$, the victim loses that fixed rate:
     $$\text{Victim Flags Score} = \text{Victim Flags Score} - \text{game.flag\_stolen\_rate}$$
   * Otherwise, the victim loses the same amount awarded to the attacker:
     $$\text{Victim Flags Score} = \text{Victim Flags Score} - (\text{flag.value} \times \text{game.flag\_captured\_multiplier})$$
3. **Capture State Lock**: The flag's `captured_team_id` is set to the attacker's ID, locking it against further capture attempts.
4. **Hint System**: The system returns the description of a random, uncaptured flag owned by the same victim team to help the attacker navigate their next exploit.

---

## 5. Practical Scoring Scenarios

To help illustrate how these rules interact in game conditions, consider the following simulated scenarios:

### Scenario A: Service Uptime Evaluation
* **Setup**: Team Blue 1 has a host `webserver` with two services: HTTP (port 80) and SSH (port 22).
  * *Service 1 (HTTP)*: Value = 50, Status = 0 (Up). Has a content checker returning a 90% status rating.
  * *Service 2 (SSH)*: Value = 50, Status = 4 (Yellow/Warning).
* **Evaluation**:
  1. *HTTP service score*: $\lfloor 50 \times 0.90 \rfloor = 45$ points.
  2. *SSH service score*: $\lfloor 50 \times 0.50 \rfloor = 25$ points.
  3. *Host Uptime Score*: $45 + 25 = 70$ points.
* **Result**: Team Blue 1 scores **70 points** for their `webserver` uptime this round.

### Scenario B: Active Red Team Beacon Compromise
* **Setup**: Red Team registers a new beacon compromise on Blue Team 1's `db-server` FQDN. The game's `beacon_value` is set to 100 points.
* **Evaluation**:
  1. *Registration (Immediate)*: Red Team receives a one-time reward of **+100 points** added to their `score_beacons`.
  2. *Tick 1*: The compromise is active. Blue Team 1 loses **-100 points** from their `score_beacons`. Red Team receives **0** points this tick.
  3. *Tick 2*: The compromise remains active. Blue Team 1 loses another **-100 points** (total -200 points).
  4. *Cleanup*: Blue Team 1 cleans the system and closes the compromise. Tick 3 executes with no deductions.
* **Result**: Red Team nets a permanent +100 points. Blue Team 1 loses 200 points over the course of two scoring rounds.

### Scenario C: Flag Stolen / Captured Points
* **Setup**: Red Team captures the "Database Secret" flag (Value = 100) from Blue Team 1's server. The game configuration has:
  * `flag_captured_multiplier = 3`
  * `flag_stolen_rate = 150`
* **Evaluation**:
  1. *Attacker (Red Team)*: Receives $\text{value} \times \text{multiplier} = 100 \times 3 = 300$ points.
  2. *Victim (Blue Team 1)*: Deducted the fixed `flag_stolen_rate` of **-150 points**. (If `flag_stolen_rate` were set to 0, the victim would have been deducted -300 points instead).
* **Result**: Red Team gains 300 points, and Blue Team 1 loses 150 points.

