# Scorebot Core vs. Scorebot Core Lite

This document highlights the key differences in architecture, database storage, API structures, deployment footprints, and maintainability between **Scorebot Core (Django-based)** and **Scorebot Core Lite (FastAPI-based)**.

---

## High-Level Comparison

| Feature | Scorebot Core (Django) | Scorebot Core Lite (FastAPI) |
| :--- | :--- | :--- |
| **Framework** | Django 4.x / 5.x | FastAPI (ASGI / Starlette) |
| **Database Support** | PostgreSQL / MySQL / Oracle | SQLite (optimized) / PostgreSQL / MySQL |
| **Routing Model** | Synchronous (Django ORM & Views) | Asynchronous (`async`/`await` routing) |
| **Background Jobs** | Celery / external cron / management daemons | Internal multi-threaded `SchedulerDaemon` |
| **System Footprint** | Large (requires Redis/RabbitMQ, PG database) | Small (standalone python process + SQLite WAL) |
| **Deployment Complexity**| High (requires multiple systemd units/containers) | Very Low (single systemd service or Docker container) |
| **Ingestion Engine** | Django Admin / manual migrations / custom models | REST endpoints `/api/admin/games/import` / JSON blobs |

---

## Architectural & Framework Differences

### 1. Monolithic vs. Lightweight Design
* **Scorebot Core** leverages Django's mature monolithic model. It includes Django Admin interface, Django ORM, and complex relational models split across multiple apps (`scorebot_core`, `scorebot_game`, `scorebot_grid`, `scorebot_html`). It is designed to act as an enterprise-grade CTF orchestration tool but requires setting up message brokers (like Redis) and process executors.
* **Scorebot Core Lite** is refactored around FastAPI. It strips out the heavy template engines and ORM overhead in favor of a lean SQLAlchemy layer. It serves the scoreboard, ticket manager, beacon receiver, and admin dashboard as a unified async service.

### 2. Database Paradigms
* **Scorebot Core** expects PostgreSQL or MySQL, taking advantage of transactional isolation levels.
* **Scorebot Core Lite** is built with SQLite compatibility at its center. Through custom connection pooling hooks (`models.py`) and write-ahead logging (WAL) optimizations, Core Lite achieves safe, high-speed concurrent writes on SQLite, removing the operational overhead of hosting separate database servers.

---

## API & Game Ingestion Models

### Ingestion Pipeline
* In **Scorebot Core**, importing events is traditionally done through Django management commands or custom SQL/Django Admin migrations.
* In **Scorebot Core Lite**, ingestion is completely modernized around automated REST ingestion (`ingest.py`). The GitOps pipeline compiles game definitions into a JSON payload and directly posts it to `/api/admin/games/import`. This allows game definition updates to be decoupled from the core database state.

### Metrics & Monitoring
* **Scorebot Core Lite** features native Prometheus metrics endpoint on `/metrics` which reports team scores, uptime, open ticket counts, and service states out-of-the-box. Scorebot Core requires third-party plugins or custom view logic to expose Prometheus formatted metrics.

---

## Summary: When to Use Which?

* **Choose Scorebot Core** if you are running massive, multi-day enterprise-scale exercises requiring separate worker nodes, dedicated Django Admin user-management systems, and database clustering.
* **Choose Scorebot Core Lite** if you need a rapid, highly automated deployment (e.g. dynamic range deployment using Terraform and Ansible), lightweight resource consumption, and simplified GitOps management.
