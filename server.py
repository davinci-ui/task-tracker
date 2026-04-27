"""
Falafel Brothers — Task Tracker
FastAPI backend with SQLite on Docker volume

Three views:
- Projects: parent items with dropdown subtasks
- Tasks: standalone checklist items (no subtasks)
- Done: completed projects or tasks (expandable + uncheckable)
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


# ── Pydantic Models ────────────────────────────────────

class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    category_id: Optional[int] = None
    project: Optional[str] = None
    tags: Optional[str] = None
    due_date: Optional[str] = None
    parent_id: Optional[int] = None
    item_type: Optional[str] = None  # "project" or "task"


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    category_id: Optional[int] = None
    project: Optional[str] = None
    tags: Optional[str] = None
    due_date: Optional[str] = None


# ── Helpers ────────────────────────────────────────────

def _row_to_dict(row, conn=None):
    """Convert a sqlite3.Row to dict. Optionally adds subtask_count for projects."""
    result = {
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
        "parent_id": row["parent_id"],
        "sort_order": row["sort_order"],
        "item_type": row["item_type"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"] if row["updated_at"] else None,
    }
    if row["item_type"] == "project" and conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE parent_id = ?",
            (row["id"],)
        ).fetchone()[0]
        done_count = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE parent_id = ? AND status = 'done'",
            (row["id"],)
        ).fetchone()[0]
        result["subtask_count"] = {"total": total, "done": done_count}
    return result


def _check_parent_complete(conn, parent_id):
    """If all subtasks of a parent are done, auto-complete the parent."""
    now = datetime.now().isoformat()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE parent_id = ? AND status != 'done'",
        (parent_id,)
    ).fetchone()[0]
    if remaining == 0:
        parent_row = conn.execute(
            "SELECT id, title, category_id FROM todos WHERE id = ? AND status != 'done'",
            (parent_id,)
        ).fetchone()
        if parent_row:
            conn.execute(
                "UPDATE todos SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                (now, now, parent_row["id"])
            )
            if parent_row["category_id"]:
                conn.execute(
                    "INSERT INTO daily_log (date, category_id, entry, source, todo_id) VALUES (?, ?, ?, 'todo', ?)",
                    (date.today().isoformat(), parent_row["category_id"], parent_row["title"], parent_row["id"])
                )
            return parent_row["id"]
    return None


def init_db():
    """Create tables and auto-migrate old schemas."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()]
        if len(cols) > 0 and "item_type" not in cols:
            conn.execute("ALTER TABLE todos ADD COLUMN item_type TEXT DEFAULT 'task'")
            conn.execute("""
                UPDATE todos SET item_type = 'project'
                WHERE parent_id IS NULL
                  AND id IN (SELECT DISTINCT parent_id FROM todos WHERE parent_id IS NOT NULL)
            """)
            conn.commit()

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
                parent_id INTEGER,
                sort_order INTEGER DEFAULT 0,
                item_type TEXT DEFAULT 'task',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (parent_id) REFERENCES todos(id) ON DELETE CASCADE
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
            CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
            CREATE INDEX IF NOT EXISTS idx_todos_parent ON todos(parent_id);
            CREATE INDEX IF NOT EXISTS idx_todos_type ON todos(item_type);
            CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date);
            CREATE INDEX IF NOT EXISTS idx_todos_category ON todos(category_id);
            CREATE INDEX IF NOT EXISTS idx_log_date ON daily_log(date);
        """)
    finally:
        conn.close()


# ── App Setup ──────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def index() -> HTMLResponse:
    index_path = "/app/static/index.html"
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Task Tracker API</h1>")


@app.get("/api/categories")
def get_categories():
    return CATEGORIES


# ── Todo List ──────────────────────────────────────────

@app.post("/api/todos")
def create_todo(todo: TodoCreate):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        sort_order = 0
        item_type = todo.item_type or ("project" if todo.parent_id is None else "task")

        if todo.parent_id:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM todos WHERE parent_id = ?",
                (todo.parent_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE todos SET item_type = 'project' WHERE id = ? AND item_type != 'project'",
                (todo.parent_id,)
            )

        cursor = conn.execute(
            """INSERT INTO todos (title, description, priority, category_id, project, tags, due_date, parent_id, sort_order, item_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (todo.title, todo.description, todo.priority, todo.category_id,
             todo.project, todo.tags, todo.due_date, todo.parent_id, sort_order,
             item_type, datetime.now().isoformat())
        )
        conn.commit()
        return {"id": cursor.lastrowid, "title": todo.title, "item_type": item_type, "status": "created"}
    finally:
        conn.close()


@app.get("/api/todos")
def list_todos(
    status: Optional[str] = None,
    category_id: Optional[int] = None,
    priority: Optional[str] = None,
    include_done: bool = False,
    parent_id: Optional[str] = None,
    item_type: Optional[str] = None,
):
    """List todos.
    - Default: only non-done items
    - status=done: show done only
    - status=todo|in-progress: filter by that
    - parent_id=N: subtasks for that parent
    - item_type=project|task: filter by type
    """
    pid = None
    if parent_id is not None:
        try:
            pid = int(parent_id)
        except ValueError:
            pass

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM todos WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status)
        elif not include_done and pid is None:
            # Only filter out done items when listing root items
            # Subtasks should always show regardless of status
            query += " AND status != 'done'"

        if category_id:
            query += " AND category_id = ?"
            params.append(category_id)

        if priority:
            query += " AND priority = ?"
            params.append(priority)

        if item_type:
            query += " AND item_type = ?"
            params.append(item_type)

        if pid is not None and pid > 0:
            query += " AND parent_id = ?"
            params.append(pid)
        else:
            query += " AND parent_id IS NULL"

        query += " ORDER BY due_date, CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END"

        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r, conn) for r in rows]
    finally:
        conn.close()


@app.get("/api/todos/projects")
def list_projects(status: Optional[str] = None):
    """Projects view: parent items with subtasks."""
    return list_todos(status=status, item_type="project")


@app.get("/api/todos/tasks")
def list_tasks(status: Optional[str] = None):
    """Tasks view: standalone items."""
    return list_todos(status=status, item_type="task")


@app.get("/api/todos/{todo_id}")
def get_todo(todo_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")
        result = _row_to_dict(row, conn)
        if row["item_type"] == "project":
            sub_rows = conn.execute(
                "SELECT id, title, priority, status, due_date, completed_at, sort_order FROM todos WHERE parent_id = ? ORDER BY sort_order, due_date",
                (todo_id,)
            ).fetchall()
            result["subtasks"] = [
                {"id": r["id"], "title": r["title"], "priority": r["priority"],
                 "status": r["status"], "due_date": r["due_date"],
                 "completed_at": r["completed_at"], "sort_order": r["sort_order"]}
                for r in sub_rows
            ]
        return result
    finally:
        conn.close()


@app.put("/api/todos/{todo_id}")
def update_todo(todo_id: int, update: TodoUpdate):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f"Todo #{todo_id} not found")

        sets = []
        vals = []
        now = datetime.now().isoformat()

        for field in ["title", "description", "priority", "category_id", "project", "tags", "due_date"]:
            v = getattr(update, field)
            if v is not None:
                sets.append(f"{field} = ?")
                vals.append(v)

        if update.status is not None:
            sets.append("status = ?")
            vals.append(update.status)
            if update.status == "done":
                sets.append("completed_at = ?")
                vals.append(now)
                if existing["parent_id"]:
                    _check_parent_complete(conn, existing["parent_id"])
                if existing["parent_id"] is None and existing["category_id"]:
                    conn.execute(
                        "INSERT INTO daily_log (date, category_id, entry, source, todo_id) VALUES (?, ?, ?, 'todo', ?)",
                        (date.today().isoformat(), existing["category_id"], existing["title"], todo_id)
                    )

        if sets:
            sets.append("updated_at = ?")
            vals.append(now)
            vals.append(todo_id)
            conn.execute(f'UPDATE todos SET {", ".join(sets)} WHERE id = ?', vals)
            conn.commit()
        return {"id": todo_id, "status": "updated"}
    finally:
        conn.close()


@app.post("/api/todos/{todo_id}/complete")
def complete_todo(todo_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")
        now = datetime.now().isoformat()
        conn.execute("UPDATE todos SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                     (now, now, todo_id))
        if row["parent_id"]:
            _check_parent_complete(conn, row["parent_id"])
        elif row["category_id"]:
            conn.execute(
                "INSERT INTO daily_log (date, category_id, entry, source, todo_id) VALUES (?, ?, ?, 'todo', ?)",
                (date.today().isoformat(), row["category_id"], row["title"], todo_id)
            )
        conn.commit()
        return {"id": todo_id, "title": row["title"], "status": "completed"}
    finally:
        conn.close()


@app.post("/api/todos/{todo_id}/uncomplete")
def uncomplete_todo(todo_id: int):
    """Uncheck a done item — resets it back to todo."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")

        now = datetime.now().isoformat()
        conn.execute("UPDATE todos SET status = 'todo', completed_at = NULL, updated_at = ? WHERE id = ?",
                     (now, todo_id))

        # If this is a parent project, also uncomplete all its subtasks
        if row["item_type"] == "project" or row["parent_id"] is None:
            # Check if it has subtasks — if so, reset them too
            subs = conn.execute("SELECT id FROM todos WHERE parent_id = ? AND status = 'done'", (todo_id,)).fetchall()
            for sub in subs:
                conn.execute("UPDATE todos SET status = 'todo', completed_at = NULL WHERE id = ?", (sub["id"],))

        conn.commit()
        return {"id": todo_id, "title": row["title"], "status": "reset"}
    finally:
        conn.close()


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT id FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")
        conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return {"id": todo_id, "status": "deleted"}
    finally:
        conn.close()


# ── Daily Log ──────────────────────────────────────────

@app.post("/api/logs")
def add_log(entry: dict):
    log_date = entry.get("log_date") or date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            "INSERT INTO daily_log (date, category_id, entry, sub_entry) VALUES (?, ?, ?, ?)",
            (log_date, entry["category_id"], entry["entry"], entry.get("sub_entry"))
        )
        conn.commit()
        return {"id": cursor.lastrowid, "date": log_date, "entry": entry["entry"]}
    finally:
        conn.close()


@app.get("/api/logs")
def list_logs(log_date: Optional[str] = None, category_id: Optional[int] = None):
    query = "SELECT * FROM daily_log WHERE 1=1"
    params = []
    if log_date:
        query += " AND date = ?"
        params.append(log_date)
    if category_id:
        query += " AND category_id = ?"
        params.append(category_id)
    query += " ORDER BY category_id, id DESC"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
        return [{"id": r["id"], "date": r["date"], "category_id": r["category_id"],
                 "category_name": CATEGORIES.get(r["category_id"], "?"),
                 "entry": r["entry"], "sub_entry": r["sub_entry"],
                 "source": r["source"], "todo_id": r["todo_id"]}
                for r in rows]
    finally:
        conn.close()


# ── Stats ──────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done'").fetchone()[0]
        finished = conn.execute("SELECT COUNT(*) FROM todos WHERE status = 'done'").fetchone()[0]
        projects_active = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE status != 'done' AND item_type = 'project'").fetchone()[0]
        tasks_active = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE status != 'done' AND item_type = 'task'").fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE status != 'done' AND priority = 'high'").fetchone()[0]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE status != 'done' AND parent_id IS NULL AND due_date < ?",
            (date.today().isoformat(),)
        ).fetchone()[0]
        return {
            "total": total, "active": active, "completed": finished,
            "projects_active": projects_active, "tasks_active": tasks_active,
            "high_priority": high, "overdue": overdue,
        }
    finally:
        conn.close()


@app.get("/api/backup")
def trigger_backup():
    backup_db()
    backups = sorted(os.listdir(BACKUP_DIR))[-10:] if os.path.exists(BACKUP_DIR) else []
    return {"status": "backup_created", "recent_backups": backups}
