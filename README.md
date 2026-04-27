# Falafel Brothers — Task Tracker

A clean, self-hosted task management app with daily logging. 14 categories, priority tracking, due dates, and project tagging.

Built for **Falafel Brothers** (Tokyo) — but works anywhere.

## Quick Start

```bash
git clone https://github.com/your-org/task-tracker.git
cd task-tracker
docker compose up -d
```

Open [http://localhost:7780](http://localhost:7780).

## What's Inside

| Service | URL | Description |
|---------|-----|-------------|
| Web UI | http://localhost:7780 | Task list, filters, add/edit/complete |
| API | http://localhost:7780/api | FastAPI JSON API |
| Stats | http://localhost:7780/api/stats | Dashboard summary |

## API Reference

### Todos
- `GET /api/todos` — List tasks (filter: `?status=todo|in-progress|done&category_id=N&priority=high|medium|low`)
- `POST /api/todos` — Create task `{ title, description, priority, category_id, project, tags, due_date }`
- `GET /api/todos/:id` — Get single task
- `PUT /api/todos/:id` — Update task fields
- `DELETE /api/todos/:id` — Delete task
- `POST /api/todos/:id/complete` — Mark done + auto-log to daily log

### Daily Log
- `GET /api/logs` — List log entries (filter: `?date=2026-04-27&category_id=N`)
- `POST /api/logs` — Add entry `{ entry, category_id, sub_entry, log_date }`
- `POST /api/logs/:id/subs` — Add sub-entry to existing log

### Categories
- `GET /api/categories` — List 14 categories with IDs

### Stats
- `GET /api/stats` — Summary (active, high priority, overdue, category breakdown)

### Backup
- `GET /api/backup` — Trigger manual DB backup

## Architecture

```
┌─────────────────┐
│   Web UI        │  ←  Single-page HTML/JS
│   (static)      │
├─────────────────┤
│  FastAPI        │  server.py
│  /api/...       │
├─────────────────┤
│  SQLite         │  /data/task_tracker.db
│                  │  (Docker volume)
│  Backups        │  /data/backups/
└─────────────────┘
```

## Data

### Categories
1. PR & Branding
2. Events
3. Creative Production
4. Retail
5. E-Commerce
6. B2B
7. Brothers @ Home
8. Sales Expansion
9. Framework
10. Collaboration
11. Training
12. Budget
13. IT
14. Future Planning

## Backup & Restore

Backups are automatic on container start. Manual backup via `GET /api/backup`.

Backups live in `/data/backups/` inside the Docker volume.

To access volume data:
```bash
docker run --rm -v task-tracker_task-tracker-data:/data -it alpine ls /data
```

## Dev

```bash
# Run without Docker
pip install -r requirements.txt
uvicorn server:app --reload --port 7780
```

## License

Internal use — Falafel Brothers.
