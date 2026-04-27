"""
Falafel Brothers - Project & Task Tracker
FastAPI backend with SQLite on Docker volume
"""

import sqlite3
import os
import shutil
from datetime import datetime, date
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Task Tracker")

DB_PATH = "/data/task_tracker.db"
BACKUP_DIR = "/data/backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

CATEGORIES = {
    1: "PR & Branding",
    2: "Events",
    3: "Creative Production",
    4: "Retail",
    5: "E-Commerce",
    6: "B2B",
    7: "Brothers @ Home",
    8: "Sales Expansion",
    9: "Framework",
    10: "Collaboration",
    11: "Training",
    12: "Budget",
    13: "IT",
    14: "Future Planning",
}

# ── Models ─────────────────────────────────────────────


class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"  # high, medium, low
    category_id: Optional[int] = None
    project: Optional[str] = None
    tags: Optional[str] = None
    due_date: Optional[str] = None


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # todo, in-progress, done
    priority: Optional[str] = None
    category_id: Optional[int] = None
    project: Optional[str] = None
    tags: Optional[str] = None
    due_date: Optional[str] = None


class LogCreate(BaseModel):
    entry: str
    category_id: int
    sub_entry: Optional[str] = None
    log_date: Optional[str] = None


# ── DB Helpers ─────────────────────────────────────────────


def init_db():
    """Initialize database with tables and schema."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'todo',
                category_id INTEGER,
                project TEXT,
                tags TEXT,
                due_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                category_id INTEGER,
                entry TEXT NOT NULL,
                sub_entry TEXT,
                source TEXT DEFAULT 'manual',
                todo_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS daily_log_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                daily_log_id INTEGER NOT NULL,
                sub_entry TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (daily_log_id) REFERENCES daily_log(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
            CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date);
            CREATE INDEX IF NOT EXISTS idx_todos_category ON todos(category_id);
            CREATE INDEX IF NOT EXISTS idx_log_date ON daily_log(date);
            CREATE INDEX IF NOT EXISTS idx_log_category ON daily_log(category_id);
        """)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


def backup_db():
    """Create timestamped backup copy."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, backup_path)


# ── Todo Endpoints ─────────────────────────────────────────────


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/categories")
def get_categories():
    return CATEGORIES


@app.post("/api/todos")
def create_todo(todo: TodoCreate):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO todos (title, description, priority, category_id, project, tags, due_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (todo.title, todo.description, todo.priority, todo.category_id, todo.project, todo.tags, todo.due_date,
             datetime.now().isoformat()),
        )
        conn.commit()
        todo_id = cursor.lastrowid

    return {"id": todo_id, "title": todo.title, "status": "created"}


@app.get("/api/todos")
def list_todos(status: Optional[str] = None, category_id: Optional[int] = None, priority: Optional[str] = None,
               include_done: bool = False):
    with get_db() as conn:
        query = "SELECT id, title, description, priority, status, category_id, project, tags, due_date, created_at, completed_at FROM todos WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status)
        elif not include_done:
            query += " AND status != 'done'"

        if category_id:
            query += " AND category_id = ?"
            params.append(category_id)

        if priority:
            query += " AND priority = ?"
            params.append(priority)

        query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, due_date"
        rows = conn.execute(query, params).fetchall()

    todos = []
    for row in rows:
        todos.append({
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "priority": row["priority"],
            "status": row["status"],
            "category_id": row["category_id"],
            "category_name": CATEGORIES.get(row["category_id"], "Unknown"),
            "project": row["project"],
            "tags": row["tags"],
            "due_date": row["due_date"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        })
    return todos


@app.get("/api/todos/{todo_id}")
def get_todo(todo_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT id, title, description, priority, status, category_id, project, tags, due_date, created_at, updated_at, completed_at FROM todos WHERE id = ?", (todo_id,)).fetchone()

    if not row:
        raise HTTPException(404, f'Todo #{todo_id} not found')

    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "priority": row["priority"],
        "status": row["status"],
        "category_id": row["category_id"],
        "category_name": CATEGORIES.get(row["category_id"], "Unknown"),
        "project": row["project"],
        "tags": row["tags"],
        "due_date": row["due_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


@app.put("/api/todos/{todo_id}")
def update_todo(todo_id: int, update: TodoUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT id, status FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f'Todo #{todo_id} not found')

        updates = []
        params = []
        now = datetime.now().isoformat()

        for field in ['title', 'description', 'priority', 'category_id', 'project', 'tags', 'due_date']:
            val = getattr(update, field)
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(val)

        if update.status is not None:
            updates.append("status = ?")
            params.append(update.status)
            if update.status == 'done' and existing['status'] != 'done':
                updates.append("completed_at = ?")
                params.append(now)
                # Auto-add to daily log
                title_row = conn.execute("SELECT title, category_id FROM todos WHERE id = ?", (todo_id,)).fetchone()
                if title_row:
                    conn.execute(
                        "INSERT INTO daily_log (date, category_id, entry, source, todo_id) VALUES (?, ?, ?, 'todo', ?)",
                        (date.today().isoformat(), title_row['category_id'], title_row['title'], todo_id)
                    )

        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(todo_id)
            conn.execute(f'UPDATE todos SET {", ".join(updates)} WHERE id = ?', params)
            conn.commit()

        return {"id": todo_id, "status": "updated"}


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f'Todo #{todo_id} not found')

        conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()

    return {"id": todo_id, "status": "deleted"}


@app.post("/api/todos/{todo_id}/complete")
def complete_todo(todo_id: int):
    """Mark todo as done and auto-log it."""
    with get_db() as conn:
        row = conn.execute("SELECT id, title, category_id, status FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f'Todo #{todo_id} not found')

        now = datetime.now().isoformat()
        conn.execute("UPDATE todos SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                     (now, now, todo_id))

        if row['category_id']:
            conn.execute(
                "INSERT INTO daily_log (date, category_id, entry, source, todo_id) VALUES (?, ?, ?, 'todo', ?)",
                (date.today().isoformat(), row['category_id'], row['title'], todo_id)
            )
        conn.commit()

    return {"id": todo_id, "title": row['title'], "status": "completed"}


# ── Daily Log Endpoints ─────────────────────────────────────


@app.post("/api/logs")
def add_log(log_entry: LogCreate):
    log_date = log_entry.log_date or date.today().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO daily_log (date, category_id, entry, sub_entry) VALUES (?, ?, ?, ?)",
            (log_date, log_entry.category_id, log_entry.entry, log_entry.sub_entry)
        )
        conn.commit()
        log_id = cursor.lastrowid

    return {"id": log_id, "date": log_date, "entry": log_entry.entry}


@app.get("/api/logs")
def list_logs(log_date: Optional[str] = None, category_id: Optional[int] = None):
    with get_db() as conn:
        query = """SELECT dl.id, dl.date, dl.category_id, dl.entry, dl.sub_entry, dl.source, dl.todo_id
                    FROM daily_log dl WHERE 1=1"""
        params = []

        if log_date:
            query += " AND dl.date = ?"
            params.append(log_date)

        if category_id:
            query += " AND dl.category_id = ?"
            params.append(category_id)

        query += " ORDER BY dl.category_id, dl.id DESC"
        rows = conn.execute(query, params).fetchall()

    logs = []
    for row in rows:
        logs.append({
            "id": row["id"],
            "date": row["date"],
            "category_id": row["category_id"],
            "category_name": CATEGORIES.get(row["category_id"], "?"),
            "entry": row["entry"],
            "sub_entry": row["sub_entry"],
            "source": row["source"],
            "todo_id": row["todo_id"],
        })
    return logs


@app.post("/api/logs/{log_id}/subs")
def add_log_sub(log_id: int, sub_content: LogCreate):
    """Add a sub-entry to a daily log entry."""
    with get_db() as conn:
        # Get max sort_order
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM daily_log_subs WHERE daily_log_id = ?", (log_id,)).fetchone()
        next_order = max_order[0] + 1

        conn.execute(
            "INSERT INTO daily_log_subs (daily_log_id, sub_entry, sort_order) VALUES (?, ?, ?)",
            (log_id, sub_content.entry, next_order)
        )
        conn.commit()

    return {"daily_log_id": log_id, "sub_entry": sub_content.entry, "sort_order": next_order}


# ── Stats & Summary Endpoints ─────────────────────────────────────


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM todos WHERE status = 'done'").fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done' AND priority = 'high'").fetchone()[0]
        overdue = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done' AND due_date < ?",
                               (date.today().isoformat(),)).fetchone()[0]

        # Category breakdown
        cat_rows = conn.execute("""
            SELECT c.id, c.name,
                   COUNT(t.id) as total,
                   SUM(CASE WHEN t.status != 'done' THEN 1 ELSE 0 END) as active
            FROM (
                SELECT 1 as id, 'PR & Branding' as name UNION ALL
                SELECT 2, 'Events' UNION ALL SELECT 3, 'Creative Production' UNION ALL
                SELECT 4, 'Retail' UNION ALL SELECT 5, 'E-Commerce' UNION ALL
                SELECT 6, 'B2B' UNION ALL SELECT 7, 'Brothers @ Home' UNION ALL
                SELECT 8, 'Sales Expansion' UNION ALL SELECT 9, 'Framework' UNION ALL
                SELECT 10, 'Collaboration' UNION ALL SELECT 11, 'Training' UNION ALL
                SELECT 12, 'Budget' UNION ALL SELECT 13, 'IT' UNION ALL
                SELECT 14, 'Future Planning'
            ) c
            LEFT JOIN todos t ON c.id = t.category_id
            GROUP BY c.id, c.name
            ORDER BY c.id
        """).fetchall()

    categories = [{"id": r["id"], "name": r["name"], "total": r["total"], "active": r["active"]} for r in cat_rows]

    return {
        "total_todos": total,
        "active_todos": active,
        "completed_todos": completed,
        "high_priority": high,
        "overdue": overdue,
        "categories": categories,
        "last_updated": datetime.now().isoformat(),
    }


@app.get("/api/backup")
def trigger_backup():
    """Trigger a manual backup and return backup file info."""
    backup_db()
    # List recent backups
    backups = sorted(os.listdir(BACKUP_DIR))[-10:]  # Last 10
    return {"status": "backup_created", "recent_backups": backups}


# ── Serve UI ─────────────────────────────────────────


@app.get("/")
def index() -> HTMLResponse:
    index_path = "/app/static/index.html"
    if os.path.exists(index_path):
        with open(index_path, 'r') as f:
            content = f.read()
        return HTMLResponse(content)
    return HTMLResponse("<h1>Task Tracker API</h1><p>Add static/index.html for the UI.</p>")
