"""
Falafel Brothers — Task Tracker
FastAPI backend with SQLite on Docker volume

Three views:
- Projects: parent items with dropdown subtasks
- Tasks: standalone checklist items (no subtasks)
- Done: completed projects or tasks
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


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()]
    if "item_type" not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN item_type TEXT DEFAULT 'task'")
        conn.execute("""
            UPDATE todos SET item_type = 'project'
            WHERE parent_id IS NULL
              AND id IN (SELECT DISTINCT parent_id FROM todos WHERE parent_id IS NOT NULL)
        """)
        conn.commit()
    conn.close()

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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, backup_path)


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


def _row_to_dict(row, conn=None):
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
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
    }
    # Add subtask count for projects
    if row["item_type"] == "project" and conn:
        total = conn.execute("SELECT COUNT(*) FROM todos WHERE parent_id = ?", (row["id"],)).fetchone()[0]
        done_count = conn.execute("SELECT COUNT(*) FROM todos WHERE parent_id = ? AND status = 'done'", (row["id"],)).fetchone()[0]
        result["subtask_count"] = {"total": total, "done": done_count}
    return result


# ── Startup ────────────────────────────────────────────


@app.on_event("startup")
def startup():
    init_db()


# ── Index ──────────────────────────────────────────────


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


# ── Todos ─────────────────────────────────────────────


@app.post("/api/todos")
def create_todo(todo: TodoCreate):
    with get_db() as conn:
        sort_order = 0
        item_type = todo.item_type or ("project" if todo.parent_id is None else "task")

        if todo.parent_id:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM todos WHERE parent_id = ?",
                (todo.parent_id,)
            ).fetchone()[0]
            # Mark parent as project if not already
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
    - item_type=project → projects (root items with subtasks)
    - item_type=task → standalone tasks (root items without subtasks)
    - parent_id=N → subtasks for that parent
    """
    pid = None
    if parent_id is not None:
        try:
            pid = int(parent_id)
        except ValueError:
            pass

    query = "SELECT * FROM todos WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

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

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()

    todos = [_row_to_dict(row, conn) for row in rows]
    conn.close()
    return todos


@app.get("/api/todos/projects")
def list_projects(status: Optional[str] = None, category_id: Optional[int] = None):
    return list_todos(status=status, category_id=category_id, item_type="project")


@app.get("/api/todos/tasks")
def list_tasks(status: Optional[str] = None, category_id: Optional[int] = None):
    return list_todos(status=status, category_id=category_id, item_type="task")


@app.get("/api/todos/{todo_id}")
def get_todo(todo_id: int):
    with get_db() as conn:
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
            {
                "id": r["id"],
                "title": r["title"],
                "priority": r["priority"],
                "status": r["status"],
                "due_date": r["due_date"],
                "completed_at": r["completed_at"],
                "sort_order": r["sort_order"],
            }
            for r in sub_rows
        ]

    return result


@app.put("/api/todos/{todo_id}")
def update_todo(todo_id: int, update: TodoUpdate):
    with get_db() as conn:
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

                if existing["parent_id"] is None:
                    if existing["category_id"]:
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


@app.post("/api/todos/{todo_id}/complete")
def complete_todo(todo_id: int):
    with get_db() as conn:
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


@app.post("/api/todos/{todo_id}/uncomplete")
def uncomplete_todo(todo_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")
        conn.execute(
            "UPDATE todos SET status = 'todo', completed_at = NULL, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), todo_id)
        )
        conn.commit()
    return {"id": todo_id, "title": row["title"], "status": "reset"}


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Todo #{todo_id} not found")
        conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
    return {"id": todo_id, "status": "deleted"}


# ── Daily Log ─────────────────────────────────────────────


@app.post("/api/logs")
def add_log(entry: dict):
    log_date = entry.get("log_date") or date.today().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO daily_log (date, category_id, entry, sub_entry) VALUES (?, ?, ?, ?)",
            (log_date, entry["category_id"], entry["entry"], entry.get("sub_entry"))
        )
        conn.commit()
    return {"id": cursor.lastrowid, "date": log_date, "entry": entry["entry"]}


@app.get("/api/logs")
def list_logs(log_date: Optional[str] = None, category_id: Optional[int] = None):
    query = "SELECT * FROM daily_log WHERE 1=1"
    params = [{"key": "log_date", "val": log_date}, {"key": "category_id", "val": category_id}]
    params_clean = []
    for p in params:
        if p["val"]:
            query += f' AND {p["key"]} = ?'
            params_clean.append(p["val"])
    query += " ORDER BY category_id, id DESC"
    with get_db() as conn:
        rows = conn.execute(query, params_clean).fetchall()
    return [
        {"id": r["id"], "date": r["date"], "category_id": r["category_id"],
         "category_name": CATEGORIES.get(r["category_id"], "?"),
         "entry": r["entry"], "sub_entry": r["sub_entry"],
         "source": r["source"], "todo_id": r["todo_id"]}
        for r in rows
    ]


# ── Stats ──────────────────────────────────────────────


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done'").fetchone()[0]
        finished = conn.execute("SELECT COUNT(*) FROM todos WHERE status = 'done'").fetchone()[0]
        projects_active = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done' AND item_type = 'project'").fetchone()[0]
        tasks_active = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done' AND item_type = 'task'").fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM todos WHERE status != 'done' AND priority = 'high'").fetchone()[0]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE status != 'done' AND parent_id IS NULL AND due_date < ?",
            (date.today().isoformat(),)
        ).fetchone()[0]

    return {
        "total": total,
        "active": active,
        "completed": finished,
        "projects_active": projects_active,
        "tasks_active": tasks_active,
        "high_priority": high,
        "overdue": overdue,
    }


@app.get("/api/backup")
def trigger_backup():
    backup_db()
    backups = sorted(os.listdir(BACKUP_DIR))[-10:]
    return {"status": "backup_created", "recent_backups": backups}
