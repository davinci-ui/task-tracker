"""
Falafel Brothers - Task Tracker
FastAPI + SQLite on Docker volume.

Three views:
  Projects: parent items with collapsible subtask dropdowns
  Tasks:   standalone checklist items (check ✓ / uncheck)
  Done:    completed items — expandable; uncheck moves back
"""

import os
import shutil
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Task Tracker")

DB_PATH     = "/data/task_tracker.db"
BACKUP_DIR  = "/data/backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

CATEGORIES = {
    1: "PR & Branding",   2: "Events",          3: "Creative Production",
    4: "Retail",          5: "E-Commerce",      6: "B2B",
    7: "Brothers @ Home", 8: "Sales Expansion", 9: "Framework",
    10: "Collaboration",  11: "Training",       12: "Budget",
    13: "IT",             14: "Future Planning",
}

class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority:    str = "medium"
    category_id: Optional[int] = None
    project:     Optional[str] = None
    tags:        Optional[str] = None
    due_date:    Optional[str] = None          # required on project, optional on subtask
    parent_id:   Optional[int]  = None         # if set → subtask
    item_type:   Optional[str]  = None         # "project" | "task"

class TodoUpdate(BaseModel):
    title:        Optional[str] = None
    description:  Optional[str] = None
    status:       Optional[str] = None
    priority:     Optional[str] = None
    category_id:  Optional[int] = None
    project:      Optional[str] = None
    tags:         Optional[str] = None
    due_date:     Optional[str] = None


def _row_to_dict(row, conn=None):
    """sqlite3.Row → dict, with subtask_count for projects."""
    d = dict(row)
    d["category_name"] = CATEGORIES.get(d.get("category_id"), "Unknown")
    d["updated_at"] = d.get("updated_at") if d.get("updated_at") else None
    if d.get("item_type") == "project" and conn:
        tot  = conn.execute("SELECT COUNT(*) FROM todos WHERE parent_id=?",(d["id"],)).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM todos WHERE parent_id=? AND status='done'",(d["id"],)).fetchone()[0]
        d["subtask_count"] = {"total": tot, "done": done}
    return d

def _check_parent_complete(conn, parent_id):
    """Auto-complete parent when all subtasks are done."""
    remaining = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE parent_id=? AND status!='done'", (parent_id,)
    ).fetchone()[0]
    if remaining == 0:
        p = conn.execute("SELECT id,title,category_id FROM todos WHERE id=? AND status!='done'",(parent_id,)).fetchone()
        if p:
            now = datetime.now().isoformat()
            conn.execute("UPDATE todos SET status='done',completed_at=?,updated_at=? WHERE id=?",(now,now,p["id"]))
            if p["category_id"]:
                conn.execute(
                    "INSERT INTO daily_log(date,category_id,entry,source,todo_id) VALUES(?,?,?,?,?)",
                    (date.today().isoformat(), p["category_id"], p["title"], "todo", p["id"]))
            return p["id"]
    return None

# -- DB bootstrap / migration ------------------------------------------------
def _init_db():
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()]
    if len(cols) > 0 and "item_type" not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN item_type TEXT DEFAULT 'task'")
        conn.execute("UPDATE todos SET item_type='project' WHERE parent_id IS NULL "
                     "AND id IN(SELECT DISTINCT parent_id FROM todos WHERE parent_id IS NOT NULL)")
        conn.commit()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, description TEXT, priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'todo', category_id INTEGER, project TEXT, tags TEXT,
            due_date TEXT, parent_id INTEGER, sort_order INTEGER DEFAULT 0,
            item_type TEXT DEFAULT 'task', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT, completed_at TEXT,
            FOREIGN KEY(parent_id) REFERENCES todos(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS daily_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
            category_id INTEGER, entry TEXT NOT NULL, sub_entry TEXT,
            source TEXT DEFAULT 'manual', todo_id INTEGER);
        CREATE INDEX IF NOT EXISTS _s ON todos(status);
        CREATE INDEX IF NOT EXISTS _p ON todos(parent_id);
        CREATE INDEX IF NOT EXISTS _t ON todos(item_type);
        CREATE INDEX IF NOT EXISTS _d ON todos(due_date);
        CREATE INDEX IF NOT EXISTS _c ON todos(category_id);
        CREATE INDEX IF NOT EXISTS _dl ON daily_log(date);
    """)
    conn.close()

@app.on_event("startup")
def _startup(): _init_db()

# -- helpers -----------------------------------------------------------------
def _conn():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

def _list_todos(status=None, category_id=None, priority=None,
                include_done=False, parent_id=None, item_type=None):
    pid = None
    if parent_id is not None:
        try:  pid = int(parent_id)
        except ValueError: pass

    c = _conn()
    q = "SELECT * FROM todos WHERE 1=1"; p = []
    if status:              q += " AND status=?";    p.append(status)
    elif not include_done and pid is None:          q += " AND status!='done'"
    if category_id is not None:  q += " AND category_id=?"; p.append(category_id)
    if priority:            q += " AND priority=?";  p.append(priority)
    if item_type:           q += " AND item_type=?"; p.append(item_type)
    if pid is not None and pid > 0:
        q += " AND parent_id=?";   p.append(pid)
    else:
        q += " AND parent_id IS NULL"
    q += " ORDER BY due_date, CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
    rows = [_row_to_dict(r, c) for r in c.execute(q, p).fetchall()]
    c.close()
    return rows

# -- routes ------------------------------------------------------------------
@app.get("/")
def index() -> HTMLResponse:
    p = "/app/static/index.html"
    return HTMLResponse(open(p).read()) if os.path.exists(p) else HTMLResponse("<h1>API</h1>")

@app.get("/api/categories")
def get_categories(): return CATEGORIES

@app.post("/api/todos")
def create_todo(t: TodoCreate):
    c = _conn()
    so = 0
    it = t.item_type or ("project" if t.parent_id is None else "task")
    if t.parent_id:
        so = c.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM todos WHERE parent_id=?",(t.parent_id,)).fetchone()[0]
        c.execute("UPDATE todos SET item_type='project' WHERE id=? AND item_type!='project'",(t.parent_id,))
    cur = c.execute(
        "INSERT INTO todos(title,description,priority,category_id,project,tags,due_date,parent_id,sort_order,item_type,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (t.title, t.description, t.priority, t.category_id, t.project, t.tags,
         t.due_date, t.parent_id, so, it, datetime.now().isoformat()))
    c.commit(); rid = cur.lastrowid; c.close()
    return {"id": rid, "title": t.title, "item_type": it, "status": "created"}

@app.get("/api/todos")
def list_todos(status=None, category_id=None, priority=None,
               include_done=False, parent_id=None, item_type=None):
    return _list_todos(status, category_id, priority, include_done, parent_id, item_type)

@app.get("/api/todos/projects")
def list_projects(status=None):          return _list_todos(status=status, item_type="project")

@app.get("/api/todos/tasks")
def list_tasks(status=None):             return _list_todos(status=status, item_type="task")

@app.get("/api/todos/{todo_id}")
def get_todo(todo_id: int):
    c = _conn()
    r = c.execute("SELECT * FROM todos WHERE id=?",(todo_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404, f"#{todo_id} not found")
    d = _row_to_dict(r, c)
    if r["item_type"] == "project":
        d["subtasks"] = [_row_to_dict(s)["id"] or dict(id=s["id"],title=s["title"],
            priority=s["priority"],status=s["status"],due_date=s["due_date"],
            completed_at=s["completed_at"],sort_order=s["sort_order"])
            for s in c.execute(
            "SELECT id,title,priority,status,due_date,completed_at,sort_order FROM todos WHERE parent_id=? ORDER BY sort_order,due_date",
            (todo_id,)).fetchall()]
    c.close(); return d

@app.put("/api/todos/{todo_id}")
def update_todo(todo_id: int, u: TodoUpdate):
    c = _conn()
    ex = c.execute("SELECT * FROM todos WHERE id=?",(todo_id,)).fetchone()
    if not ex: c.close(); raise HTTPException(404, f"#{todo_id} not found")
    sets, vals, now = [], [], datetime.now().isoformat()
    for f in ["title","description","priority","category_id","project","tags","due_date"]:
        v = getattr(u, f)
        if v is not None: sets.append(f"{f}=?"); vals.append(v)
    if u.status is not None:
        sets.append("status=?"); vals.append(u.status)
        if u.status == "done":
            sets.append("completed_at=?"); vals.append(now)
            if ex["parent_id"]: _check_parent_complete(c, ex["parent_id"])
            if ex["parent_id"] is None and ex["category_id"]:
                c.execute("INSERT INTO daily_log(date,category_id,entry,source,todo_id) VALUES(?,?,?,?,?)",
                    (date.today().isoformat(), ex["category_id"], ex["title"], "todo", todo_id))
    if sets:
        sets.append("updated_at=?"); vals.append(now); vals.append(todo_id)
        c.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id=?", vals); c.commit()
    c.close(); return {"id": todo_id, "status": "updated"}

@app.post("/api/todos/{todo_id}/complete")
def complete_todo(todo_id: int):
    c = _conn()
    r = c.execute("SELECT * FROM todos WHERE id=?",(todo_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404, f"#{todo_id} not found")
    now = datetime.now().isoformat()
    c.execute("UPDATE todos SET status='done',completed_at=?,updated_at=? WHERE id=?",(now,now,todo_id))
    if r["parent_id"]: _check_parent_complete(c, r["parent_id"])
    elif r["category_id"]:
        c.execute("INSERT INTO daily_log(date,category_id,entry,source,todo_id) VALUES(?,?,?,?,?)",
            (date.today().isoformat(), r["category_id"], r["title"], "todo", todo_id))
    c.commit(); c.close()
    return {"id": todo_id, "title": r["title"], "status": "completed"}

@app.post("/api/todos/{todo_id}/uncomplete")
def uncomplete_todo(todo_id: int):
    c = _conn()
    r = c.execute("SELECT * FROM todos WHERE id=?",(todo_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404, f"#{todo_id} not found")
    now = datetime.now().isoformat()
    c.execute("UPDATE todos SET status='todo',completed_at=NULL,updated_at=? WHERE id=?",(now,todo_id))
    # If this is a subtask, also reset the parent (project no longer done)
    if r["parent_id"]:
        c.execute("UPDATE todos SET status='todo',completed_at=NULL,updated_at=? WHERE id=?",(now,r["parent_id"]))
    c.commit(); c.close()
    return {"id": todo_id, "title": r["title"], "status": "reset"}

@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    c = _conn()
    if not c.execute("SELECT id FROM todos WHERE id=?",(todo_id,)).fetchone():
        c.close(); raise HTTPException(404)
    c.execute("DELETE FROM todos WHERE id=?",(todo_id,)); c.commit(); c.close()
    return {"id": todo_id, "status": "deleted"}

@app.post("/api/logs")
def add_log(entry: dict):
    d = entry.get("log_date") or date.today().isoformat()
    c = _conn()
    cur = c.execute("INSERT INTO daily_log(date,category_id,entry,sub_entry) VALUES(?,?,?,?)",
        (d, entry["category_id"], entry["entry"], entry.get("sub_entry")))
    c.commit(); c.close()
    return {"id": cur.lastrowid, "date": d, "entry": entry["entry"]}

@app.get("/api/logs")
def list_logs(log_date=None, category_id=None):
    q, p = "SELECT * FROM daily_log WHERE 1=1", []
    if log_date:     q += " AND date=?";     p.append(log_date)
    if category_id:  q += " AND category_id=?"; p.append(category_id)
    q += " ORDER BY category_id, id DESC"
    c = _conn(); rows = c.execute(q, p).fetchall(); c.close()
    return [dict(id=r["id"],date=r["date"],category_id=r["category_id"],
        category_name=CATEGORIES.get(r["category_id"],"?"),entry=r["entry"],
        sub_entry=r["sub_entry"],source=r["source"],todo_id=r["todo_id"]) for r in rows]

@app.get("/api/stats")
def get_stats():
    c = _conn()
    r = dict(
        total           = c.execute("SELECT COUNT(*) FROM todos").fetchone()[0],
        active          = c.execute("SELECT COUNT(*) FROM todos WHERE status!='done'").fetchone()[0],
        completed       = c.execute("SELECT COUNT(*) FROM todos WHERE status='done'").fetchone()[0],
        projects_active = c.execute("SELECT COUNT(*) FROM todos WHERE status!='done' AND item_type='project'").fetchone()[0],
        tasks_active    = c.execute("SELECT COUNT(*) FROM todos WHERE status!='done' AND item_type='task'").fetchone()[0],
        high_priority   = c.execute("SELECT COUNT(*) FROM todos WHERE status!='done' AND priority='high'").fetchone()[0],
        overdue         = c.execute("SELECT COUNT(*) FROM todos WHERE status!='done' AND parent_id IS NULL AND due_date<?",(date.today().isoformat(),)).fetchone()[0])
    c.close(); return r

@app.get("/api/backup")
def trigger_backup():
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"))
    return {"status":"backup_created","recent_backups": sorted(os.listdir(BACKUP_DIR))[-10:] if os.path.exists(BACKUP_DIR) else []}
