import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "data" / "pa.db"))


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _connect()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS todos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            text       TEXT NOT NULL,
            done       INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            done_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT NOT NULL,
            text         TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_data TEXT NOT NULL,
            active       INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS message_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    c.commit()
    c.close()


def add_message(user_id: str, role: str, content: str):
    c = _connect()
    c.execute(
        "INSERT INTO message_history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    c.execute(
        """DELETE FROM message_history WHERE user_id = ? AND id NOT IN (
               SELECT id FROM message_history WHERE user_id = ?
               ORDER BY id DESC LIMIT 10
           )""",
        (user_id, user_id),
    )
    c.commit()
    c.close()


def get_history(user_id: str, n: int = 10) -> list[dict]:
    c = _connect()
    rows = c.execute(
        "SELECT role, content FROM message_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    c.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def add_todo(user_id: str, text: str) -> int:
    c = _connect()
    cur = c.execute("INSERT INTO todos (user_id, text) VALUES (?, ?)", (user_id, text))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def list_todos(user_id: str) -> list[dict]:
    c = _connect()
    rows = c.execute(
        "SELECT * FROM todos WHERE user_id = ? ORDER BY done ASC, created_at ASC",
        (user_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def complete_todo(todo_id: int) -> bool:
    c = _connect()
    cur = c.execute(
        "UPDATE todos SET done = 1, done_at = datetime('now') WHERE id = ? AND done = 0",
        (todo_id,)
    )
    c.commit()
    ok = cur.rowcount > 0
    c.close()
    return ok


def delete_todo(todo_id: int) -> bool:
    c = _connect()
    cur = c.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    c.commit()
    ok = cur.rowcount > 0
    c.close()
    return ok


def add_reminder(user_id: str, text: str, trigger_type: str, trigger_data: str) -> int:
    c = _connect()
    cur = c.execute(
        "INSERT INTO reminders (user_id, text, trigger_type, trigger_data) VALUES (?, ?, ?, ?)",
        (user_id, text, trigger_type, trigger_data)
    )
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def list_reminders(user_id: str) -> list[dict]:
    c = _connect()
    rows = c.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND active = 1 ORDER BY created_at ASC",
        (user_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def deactivate_reminder(reminder_id: int):
    c = _connect()
    c.execute("UPDATE reminders SET active = 0 WHERE id = ?", (reminder_id,))
    c.commit()
    c.close()


def cancel_reminder(reminder_id: int) -> bool:
    c = _connect()
    cur = c.execute(
        "UPDATE reminders SET active = 0 WHERE id = ? AND active = 1", (reminder_id,)
    )
    c.commit()
    ok = cur.rowcount > 0
    c.close()
    return ok
