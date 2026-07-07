# Roadmap: Automated Flag Planting Pipeline

This document outlines the high-level design, workflow requirements, and architectural planning for automating flag generation and planting within the CTFfactory range infrastructure.

---

## The Goal
Currently, flags must be manually registered in the `flags` database table of Scorebot Core Lite, and then manually placed on the target virtual machines. 

The goal of this roadmap feature is to completely automate this process, allowing game definitions to declare flag parameters and have provisioning pipelines dynamically generate, plant, and register flags without human intervention.

---

## Planned Architecture & Workflow

The automated flag lifecycle will operate as a multi-stage pipeline:

```mermaid
graph TD
    GD[Game-Definitions Declarations] -->|1. Parse Event| Prov[Ansible Provisioning Engine]
    Prov -->|2. Generate Token| Token[Generate Cryptographic FLAG{...}]
    Prov -->|3. Plant File| VM[Target Host VM]
    Prov -->|4. API Call| API[FastAPI Admin Flag Endpoint]
    API -->|5. Insert Record| DB[(SQLite/PG Database)]
```

### Phase 1: Configuration Declarations (game-definitions)
We will expand the schemas in `game-definitions` (e.g. `role/*.json` or `event/*.json`) to support declaring flag targets:

```json
{
  "role": {
    "name": "linux_webserver",
    "flags": [
      {
        "name": "Apache Configuration Key",
        "path": "/etc/apache2/flag.txt",
        "value": 100,
        "description": "Located in the Apache configuration directory"
      }
    ]
  }
}
```

### Phase 2: Orchestrated Generation & Planting (Ansible)
During range provisioning, Ansible playbooks (which compile and deploy target configurations) will handle flag operations:
1. **Dynamic Token Generation**: Generate a secure, pseudo-random flag signature (e.g., `FLAG{uuid-or-hash-string}`) on-the-fly.
2. **Planting**: Inject the generated token into the designated target file (e.g., writing to `/etc/apache2/flag.txt` on a Linux target or creating a Registry value on Windows).
3. **Caching**: Store the generated token temporarily inside the Ansible host variables to make it available for the database ingestion phase.

### Phase 3: Automated Database Registration (Admin API)
Once the range VMs are configured and their active IPs are resolved:
1. Ansible will query the running Core Lite instance.
2. We will implement a new admin API route:
   `POST /api/admin/games/{game_id}/hosts/{host_id}/flags`
3. Ansible will push the metadata and the token values:
   ```json
   {
     "name": "Apache Configuration Key",
     "flag": "FLAG{e5d6d3-9f8a-4c2b-8a7c-6e9f1a2b3c4d}",
     "value": 100,
     "description": "Located in the Apache configuration directory",
     "team_id": 3
   }
   ```
4. Core Lite will validate and insert the record into the `flags` table.

---

## Key Requirements & Considerations

* **Security Constraints**: The API endpoint used to insert flags (`POST /api/admin/.../flags`) must require `verify_admin_token` authentication to ensure contestants cannot insert or overwrite flags.
* **Aspect Ratios & Formats**: Playbooks must be compatible with multiple operating systems (Linux, FreeBSD, Windows Server) to plant flags securely regardless of environment type.
* **Error Recovery**: If a VM fails to provision or reboot, the pipeline must roll back or clean up any partially registered database flags to prevent "ghost flags" that cannot be retrieved by contestants.
