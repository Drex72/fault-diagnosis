"""SQLite store for the GUI: live sessions, their event log, and the project tracker.

One connection guarded by a lock (the server is threaded). WAL mode so a
reader never waits on a writer. The file lives in gui/data/ (git-ignored).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from faultdiag.runmeta import lagos_now

from .seed import SEED

TRACKER = {
    # table: (columns, allowed statuses)
    "milestones": (("day", "title", "detail", "status", "due"), ("todo", "doing", "done", "blocked")),
    "bom": (("item", "qty", "status", "supplier", "price_ngn", "date", "notes"),
            ("needed", "ordered", "received", "tested")),
    "equipment": (("item", "purpose", "status", "date", "notes"),
                  ("to request", "requested", "approved", "received", "returned")),
    "issues": (("kind", "title", "detail", "severity", "status", "date"), ("open", "closed")),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, mode TEXT, source TEXT, method TEXT, model TEXT, scenario TEXT,
  status TEXT, started TEXT, ended TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, t TEXT, kind TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
""" + "".join(
    f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    + ", ".join(f"{c} TEXT" for c in cols) + ");\n"
    for t, (cols, _) in TRACKER.items()
)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            if not self.db.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone():
                for table, rows in SEED.items():
                    cols = TRACKER[table][0]
                    for row in rows:
                        self.db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                        [str(row.get(c, "")) for c in cols])
                self.db.execute("INSERT INTO meta VALUES ('seeded', ?)", (lagos_now(),))
            self.db.commit()

    # ------------------------------------------------------------ sessions and events
    def recover_after_restart(self) -> list[str]:
        """Sessions left 'running' by a previous server process are marked interrupted."""
        with self.lock:
            ids = [r["id"] for r in self.db.execute("SELECT id FROM sessions WHERE status='running'")]
            for sid in ids:
                self.db.execute("UPDATE sessions SET status='interrupted', ended=? WHERE id=?", (lagos_now(), sid))
                self.db.execute("INSERT INTO events (session_id, t, kind, payload) VALUES (?, ?, ?, ?)",
                                (sid, lagos_now(), "session_end",
                                 json.dumps({"status": "interrupted", "reason": "the GUI server restarted mid-session"})))
            self.db.commit()
        return ids

    def new_session(self, sid: str, **fields) -> None:
        with self.lock:
            self.db.execute("INSERT INTO sessions (id, mode, source, method, model, scenario, status, started) "
                            "VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                            (sid, fields.get("mode"), fields.get("source"), fields.get("method"), fields.get("model"),
                             fields.get("scenario"), lagos_now()))
            self.db.commit()

    def end_session(self, sid: str, status: str, result: dict | None = None) -> None:
        with self.lock:
            self.db.execute("UPDATE sessions SET status=?, ended=?, result=? WHERE id=?",
                            (status, lagos_now(), json.dumps(result, default=str) if result else None, sid))
            self.db.commit()

    def reveal_scenario(self, sid: str, scenario: str) -> None:
        with self.lock:
            self.db.execute("UPDATE sessions SET scenario=? WHERE id=?", (scenario, sid))
            self.db.commit()

    def session(self, sid: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return _session_dict(r) if r else None

    def sessions(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM sessions ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        return [_session_dict(r) for r in rows]

    def add_event(self, session_id: str | None, kind: str, payload: dict) -> dict:
        t = lagos_now()
        with self.lock:
            cur = self.db.execute("INSERT INTO events (session_id, t, kind, payload) VALUES (?, ?, ?, ?)",
                                  (session_id, t, kind, json.dumps(payload, default=str)))
            self.db.commit()
        return {"id": cur.lastrowid, "session_id": session_id, "t": t, "kind": kind, "payload": payload}

    def events(self, after: int = 0, session_id: str | None = None, limit: int = 1000) -> list[dict]:
        q, args = "SELECT * FROM events WHERE id > ?", [after]
        if session_id:
            q += " AND session_id = ?"
            args.append(session_id)
        with self.lock:
            rows = self.db.execute(q + " ORDER BY id LIMIT ?", args + [limit]).fetchall()
        return [{"id": r["id"], "session_id": r["session_id"], "t": r["t"], "kind": r["kind"],
                 "payload": json.loads(r["payload"])} for r in rows]

    def last_event_id(self) -> int:
        with self.lock:
            r = self.db.execute("SELECT MAX(id) AS m FROM events").fetchone()
        return r["m"] or 0

    # ------------------------------------------------------------ tracker
    def _check(self, table: str, data: dict) -> dict:
        if table not in TRACKER:
            raise KeyError(table)
        cols, statuses = TRACKER[table]
        unknown = [k for k in data if k not in cols]
        if unknown:
            raise ValueError(f"unknown fields {unknown}; allowed {list(cols)}")
        if "status" in data and data["status"] not in statuses:
            raise ValueError(f"status must be one of {list(statuses)}")
        return {k: "" if v is None else str(v) for k, v in data.items()}

    def tracker(self, table: str) -> list[dict]:
        if table not in TRACKER:
            raise KeyError(table)
        with self.lock:
            return [dict(r) for r in self.db.execute(f"SELECT * FROM {table} ORDER BY id")]

    def tracker_create(self, table: str, data: dict) -> dict:
        data = self._check(table, data)
        data.setdefault("status", TRACKER[table][1][0])
        with self.lock:
            cur = self.db.execute(f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                                  list(data.values()))
            self.db.commit()
            return dict(self.db.execute(f"SELECT * FROM {table} WHERE id=?", (cur.lastrowid,)).fetchone())

    def tracker_update(self, table: str, row_id: int, data: dict) -> dict:
        data = self._check(table, data)
        with self.lock:
            if data:
                self.db.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in data)} WHERE id=?",
                                list(data.values()) + [row_id])
                self.db.commit()
            r = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        if r is None:
            raise LookupError(f"{table} row {row_id} not found")
        return dict(r)

    def tracker_delete(self, table: str, row_id: int) -> None:
        if table not in TRACKER:
            raise KeyError(table)
        with self.lock:
            self.db.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
            self.db.commit()

    def tracker_markdown(self) -> str:
        titles = {"milestones": "Plan (14 days)", "bom": "Bill of materials", "equipment": "Lab equipment requests",
                  "issues": "Issues and risks"}
        out = [f"# Project tracker\n\nExported {lagos_now()} (Africa/Lagos).\n"]
        for table, (cols, _) in TRACKER.items():
            out.append(f"\n## {titles[table]}\n")
            out.append("| " + " | ".join(c.replace("_", " ") for c in cols) + " |")
            out.append("|" + "---|" * len(cols))
            for r in self.tracker(table):
                out.append("| " + " | ".join(str(r[c] or "").replace("|", "/").replace("\n", " ") for c in cols) + " |")
        return "\n".join(out) + "\n"


def _session_dict(r) -> dict:
    d = dict(r)
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d
