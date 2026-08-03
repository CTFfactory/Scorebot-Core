# How-To Guides: Deploy and Operate Scorebot Core Lite

This guide contains step-by-step recipes for deploying, configuring, and operating Scorebot Core Lite in typical CTF environments.

* For operating dynamic scoreboard alerts and Rickrolls, see the [Scoreboard Admin Events Guide](file:///home/luftegrof/git/CTFfactory/Scorebot-Core/scorebot_core_lite/docs/events.md).

---


## How to Deploy via GitOps & Ansible

CTF environments are highly dynamic. We configure and deploy Scorebot Core Lite using a **GitOps workflow**, pulling configuration definitions from a Git repository (`game-definitions`) and applying them using Ansible playbooks.

### The Pipeline Architecture
1. **Game Definitions Repo**: Houses JSON files containing definitions for environments, events, teams, and VM roles.
2. **Terraform**: Provisions the range VMs on Proxmox, and outputs a JSON containing the resulting MAC/IP mappings.
3. **Ansible**: Connects to the Core Lite virtual machine, installs system services, pulls the compiled `game-definitions` schema, combines it with Terraform's IP map, and posts the complete bundle to the Core Lite API.

### Ingestion Playbook Example
To perform a deployment import, run the `import_game.yml` Ansible task with your environment context.

```bash
ansible-playbook -i inventory.ini playbooks/deploy_scorebot.yml \
  --extra-vars "scorebot_event_name=bsde \
                scorebot_environment=prod \
                scorebot_game_defs_file=/tmp/game-definitions-compiled.json \
                scorebot_admin_token=secure-admin-token \
                scorebot_clean_import=true"
```

#### Under the Hood: The Import Endpoint
When Ansible runs this playbook, it does the following:
* Checks the status of the local server.
* Reads the local `/tmp/game-definitions-compiled.json` (decodes `VAR_GAME_DEFINITIONS_DB` from GitHub Actions).
* Traverses your local terraform state directory (looking for `terraform-outputs/*.json`) to match host FQDNs with active IPs.
* Fires a POST request to `/api/admin/games/import` with the combined payload:
  ```json
  {
    "event": "bsde",
    "environment": "prod",
    "game_definitions_db": { ... },
    "subnets": {
      "team1": "10.64.1.0/24",
      "team2": "10.64.2.0/24"
    },
    "host_ips": {
      "webserver.team1.bsde.prod.ctf": "10.64.1.15",
      "webserver.team2.bsde.prod.ctf": "10.64.2.15"
    }
  }
  ```

---

## How to Handle Dynamic IP Mapping & Subnetting

In standard CTFs, blue teams defend their own subnets. Rather than hardcoding IP addresses in game configuration files, Scorebot Core Lite maps IPs dynamically based on two sources:

### Option A: Pre-Import Output Extraction (Recommended)
If your virtual machine provisioning tool (e.g., Terraform-Proxmox) outputs instance IPs, you can pipe them directly during the Ansible import step.

The playbooks check `terraform-outputs/*.json`, parses IP addresses from guest-agent fields, and feeds them into the `host_ips` mapping during import.

### Option B: Post-Import Endpoint Mapping
If VMs acquire DHCP leases after the game configuration has already been loaded, you can update host IPs on-the-fly.

Send a POST request to the `/api/hosts` endpoint with the host token or FQDN to bind its dynamic IP:

```bash
curl -X POST http://127.0.0.1:8000/api/hosts \
  -H "X-Scorebot-Token: secure-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "fqdn": "webserver.team1.bsde.prod.ctf",
    "ip": "10.64.1.189"
  }'
```

---

## How to Multiplex External Connections (SNI Routing & DNS)

Scorebot utilizes beacons and reverse proxy redirection for flag submissions and command execution checking. In a locked-down tournament network, you want a single entry point that directs incoming traffic to the appropriate service based on hostname (Server Name Indication - SNI) or DNS resolution.

### 1. DNS Redirection using gold-ns (DNS Octet .68)
Scorebot hosts require beacon connections. The database model generates specific DNS resolver targets (e.g. `10.64.team_id.68`) to forward lookups.
Make sure the team firewall rules allow UDP/53 traffic to the DNS server, redirecting traffic to the primary Core Lite listener.

### 2. SNI multiplexing using Nginx or HAProxy
If multiple services (Scoreboard, API server, Ticket system) share a single public IP address, configure an SNI-aware reverse proxy to route traffic:

Here is an example Nginx configuration redirecting requests based on domain names:

```nginx
# HTTP/FastAPI Proxy
server {
    listen 80;
    server_name api.prod.ctf scoreboard.prod.ctf;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Ticket system forwarding
server {
    listen 80;
    server_name tickets.prod.ctf;

    location / {
        proxy_pass http://127.0.0.1:8000/tickets;
        proxy_set_header Host $host;
    }
}
```
If you are using raw TCP multiplexing (e.g. for SSH/RDP connection mapping), configure HAProxy with SSL/SNI inspection inside the `frontend` section.
