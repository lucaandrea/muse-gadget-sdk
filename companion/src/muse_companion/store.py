from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: Path, database_url: str = ""):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.backend = "postgresql" if database_url else "sqlite"
        if database_url:
            from .database import Postgres
            self.db = Postgres(database_url)
        else:
            self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            os.chmod(path, 0o600)
            self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, role TEXT NOT NULL, revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, text TEXT NOT NULL, original TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_context(id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,project_id TEXT NOT NULL DEFAULT '',person_id TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',digest TEXT NOT NULL DEFAULT '',embedding TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY, title TEXT NOT NULL, due REAL NOT NULL, ssid TEXT DEFAULT '', state TEXT DEFAULT 'scheduled', delivered REAL, revision INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, result TEXT DEFAULT '', error TEXT DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, result TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, sender TEXT NOT NULL, priority TEXT NOT NULL, reason TEXT NOT NULL, state TEXT DEFAULT 'unread', created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, name TEXT NOT NULL, remote_id TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS blobs(id TEXT PRIMARY KEY,content BLOB NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(day TEXT NOT NULL, category TEXT NOT NULL, amount REAL DEFAULT 0, PRIMARY KEY(day, category));
            CREATE TABLE IF NOT EXISTS conversation(seq INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY, digest TEXT NOT NULL, pcm BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'queued', transcript TEXT, result TEXT, error TEXT DEFAULT '', created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS meeting_segments(meeting TEXT NOT NULL, position INTEGER NOT NULL, digest TEXT NOT NULL, pcm BLOB NOT NULL, transcript TEXT, marked INTEGER DEFAULT 0, PRIMARY KEY(meeting, position));
            CREATE TABLE IF NOT EXISTS work_revisions(task TEXT NOT NULL, revision INTEGER NOT NULL, instruction TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(task,revision));
            CREATE TABLE IF NOT EXISTS study_sessions(task TEXT PRIMARY KEY, revision INTEGER NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL, answers TEXT NOT NULL, started REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS study_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, title TEXT NOT NULL, revision INTEGER NOT NULL, answers TEXT NOT NULL, started REAL NOT NULL, ended REAL NOT NULL);
        """)
        if "revision" not in {r["name"] for r in self.rows("PRAGMA table_info(tasks)")}:
            self.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "context" not in {r["name"] for r in self.rows("PRAGMA table_info(captures)")}:
            self.execute("ALTER TABLE captures ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
        self.execute("PRAGMA user_version=5")
    def recover(self):
        # Only the service calls this on startup, never provisioning clients.
        # Never blindly repeat an external side effect after process failure.
        self.db.execute("UPDATE tasks SET state='uncertain',error='Interrupted during execution; verify the external result before retrying' WHERE state='executing'")
        self.db.execute("UPDATE tasks SET state='queued' WHERE state='running' AND kind IN ('research','briefing','meeting','lesson','computer','studio')")
        self.db.execute("UPDATE operations SET state='uncertain' WHERE state='running'")
        self.db.execute("UPDATE captures SET state='queued' WHERE state='transcribing'")
        self.db.execute("UPDATE captures SET state='needs_review',error='Interrupted during assistant execution; check task state before retrying' WHERE state='processing'")

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            if self.db.in_transaction:
                yield
                return
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def rows(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=()) -> int:
        with self.lock:
            return self.db.execute(sql, params).rowcount

    def setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any):
        self.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def create_device(self, name: str, role="device") -> dict:
        identity, token = new_id(), secrets.token_urlsafe(32)
        self.execute("INSERT INTO devices(id,name,token_hash,role) VALUES (?,?,?,?)", (identity, name[:80], token_hash(token), role))
        return {"id": identity, "name": name[:80], "token": token, "role": role}

    def provision_owner(self, token: str):
        if len(token) < 32:
            raise ValueError("MUSE_OWNER_TOKEN must contain at least 32 characters")
        with self.transaction():
            existing = self.one("SELECT token_hash FROM devices WHERE id='hosted-owner'")
            if existing and existing["token_hash"] != token_hash(token):
                raise Conflict("Hosted owner token differs from the database. Rotate it explicitly before changing the deployment secret")
            if self.one("SELECT id FROM devices WHERE token_hash=? AND role='owner' AND revoked=0", (token_hash(token),)):
                return  # Preserve an owner's identity after a local-to-cloud restore.
            self.execute("INSERT OR IGNORE INTO devices(id,name,token_hash,role) VALUES ('hosted-owner','Companion owner',?,'owner')", (token_hash(token),))

    def put_blob(self, identity: str, content: bytes):
        if len(content) > 24 * 1024 * 1024:
            raise ValueError("Keep original documents below 24 MB")
        self.execute("INSERT INTO blobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content,created=excluded.created", (identity, content, time.time()))

    def blob(self, identity: str):
        row = self.one("SELECT content FROM blobs WHERE id=?", (identity,))
        return bytes(row["content"]) if row else None

    def authenticate(self, token: str) -> dict | None:
        return self.one("SELECT id,name,role FROM devices WHERE token_hash=? AND revoked=0", (token_hash(token),))

    def begin_operation(self, identity: str, payload: Any) -> dict | None:
        fingerprint = token_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        with self.transaction():
            existing = self.one("SELECT * FROM operations WHERE id=?", (identity,))
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("Operation ID was reused with different content")
                if existing["state"] != "done":
                    raise Conflict("Operation is in progress or its outcome needs review")
                return json.loads(existing["result"])
            self.execute("INSERT INTO operations VALUES (?,?,'running',NULL,?)", (identity, fingerprint, time.time()))
        return None

    def finish_operation(self, identity: str, result: Any):
        self.execute("UPDATE operations SET state='done',result=? WHERE id=?", (json.dumps(result), identity))

    def fail_operation(self, identity: str):
        self.execute("UPDATE operations SET state='uncertain' WHERE id=?", (identity,))

    def event(self, kind: str, payload: Any):
        self.execute("INSERT INTO events(type,payload,created) VALUES (?,?,?)", (kind, json.dumps(payload), time.time()))

    def events(self, after: int) -> list[dict]:
        return [{**row, "payload": json.loads(row["payload"])} for row in self.rows("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 50", (after,))]

    def save_memory(self, text: str, source: str, identity: str | None = None, *, project_id: str | None = None, person_id: str | None = None) -> dict:
        now = time.time()
        if identity:
            if not self.execute("UPDATE memories SET text=?,updated=? WHERE id=?", (text, now, identity)):
                raise ValueError("Memory not found")
            self.clear_memory_caches()
        else:
            identity = new_id()
            self.execute("INSERT INTO memories VALUES (?,?,?,?,?,?)", (identity, text, text, source, now, now))
        self.execute("INSERT OR IGNORE INTO memory_context(id) VALUES (?)", (identity,))
        self.execute("UPDATE memory_context SET project_id=COALESCE(?,project_id),person_id=COALESCE(?,person_id),digest='',embedding='[]' WHERE id=?", (project_id, person_id, identity))
        return self.one("SELECT m.*,c.project_id,c.person_id FROM memories m JOIN memory_context c ON c.id=m.id WHERE m.id=?", (identity,))

    def search_memories(self, query: str) -> list[dict]:
        # A literal search, not SQL or FTS syntax; the agent can try related terms.
        words = query.lower().split()[:12]
        records = self.rows("SELECT id,text,source,created,updated FROM memories ORDER BY updated DESC LIMIT 1000")
        scored = [(sum(word in row["text"].lower() for word in words), row) for row in records]
        return [row for score, row in sorted(scored, key=lambda x: x[0], reverse=True) if score or not words][:10]

    def forget_memory(self, identity: str):
        with self.transaction():
            self.execute("DELETE FROM memories WHERE id=?", (identity,))
            self.clear_memory_caches()
        self.event("memory.changed", {"id": identity, "deleted": True})

    def clear_memory_caches(self):
        # Keep operation fingerprints so retrying old commands cannot repeat writes.
        self.execute("UPDATE operations SET result=? WHERE state='done'", (json.dumps({"text": "Cached result cleared after a memory change. Ask again for current information.", "forgotten": True}),))
        self.execute("DELETE FROM events WHERE type IN ('answer','memory')")
        self.execute("DELETE FROM conversation")
        self.event("memory.invalidated", {})

    def history(self):
        rows = self.rows("SELECT role,text FROM conversation ORDER BY seq DESC LIMIT 12")
        return [{"role": row["role"], "content": row["text"]} for row in reversed(rows)]

    def remember_turn(self, text, answer):
        self.execute("INSERT INTO conversation(role,text) VALUES ('user',?)", (text[:4000],))
        self.execute("INSERT INTO conversation(role,text) VALUES ('assistant',?)", (answer[:4000],))
        self.execute("DELETE FROM conversation WHERE seq NOT IN (SELECT seq FROM conversation ORDER BY seq DESC LIMIT 12)")

    def reminder(self, title: str, due: float, ssid: str = "") -> dict:
        identity = new_id()
        self.execute("INSERT INTO reminders(id,title,due,ssid) VALUES (?,?,?,?)", (identity, title, due, ssid))
        item = self.one("SELECT * FROM reminders WHERE id=?", (identity,))
        self.event("reminders.changed", {"id": identity})
        return item

    def due_reminders(self, now: float, ssid: str = "") -> list[dict]:
        with self.transaction():
            due = self.rows("SELECT * FROM reminders WHERE state='scheduled' AND due<=? AND (ssid='' OR ssid=?)", (now, ssid))
            for item in due:
                self.execute("UPDATE reminders SET state='due',delivered=? WHERE id=?", (now, item["id"]))
                self.event("reminder.due", item)
        return due

    def reschedule_reminder(self, identity, title, due, ssid=""):
        with self.transaction():
            if not self.execute("UPDATE reminders SET title=?,due=?,ssid=?,state='scheduled',delivered=NULL,revision=revision+1 WHERE id=?", (title, due, ssid, identity)):
                raise ValueError("Reminder not found")
            self.event("reminders.changed", {"id": identity})
            return self.one("SELECT * FROM reminders WHERE id=?", (identity,))

    def revise_work(self, identity, instruction):
        with self.transaction():
            task = self.one("SELECT * FROM tasks WHERE id=?", (identity,))
            if not task or task["kind"] not in ("research", "briefing", "lesson", "meeting", "studio") or task["state"] not in ("queued", "running", "completed", "failed"):
                raise Conflict("Only analysis work can be revised; external actions require a new proposal")
            if not instruction.strip() or len(instruction) > 12000 or task["revision"] >= 100:
                raise ValueError("Provide a correction of up to 12000 characters; start a new task after 100 revisions")
            payload = json.loads(task["payload"])
            corrections = payload.get("corrections", []) + [instruction]
            if sum(map(len, corrections)) > 24000:
                raise ValueError("This task has too many corrections. Start a new task with the current requirements")
            payload["corrections"] = corrections
            revision = task["revision"] + 1
            self.execute("UPDATE tasks SET payload=?,revision=?,state='queued',result='',error='',updated=? WHERE id=?", (json.dumps(payload), revision, time.time(), identity))
            self.execute("INSERT INTO work_revisions VALUES (?,?,?,?)", (identity, revision, instruction, time.time()))
            self.event("task.changed", {"id": identity, "state": "queued", "revision": revision})
            return self.one("SELECT * FROM tasks WHERE id=?", (identity,))

    def transition_job(self, identity, revision, state, *, result="", error="", expected="running"):
        with self.transaction():
            changed = self.execute("UPDATE tasks SET state=?,result=?,error=?,updated=? WHERE id=? AND revision=? AND state=?", (state, result, error, time.time(), identity, revision, expected))
            if changed:
                self.event("task.changed", {"id": identity, "state": state, "revision": revision})
            return bool(changed)

    def task(self, kind: str, title: str, payload: dict, state="queued") -> dict:
        identity, now = new_id(), time.time()
        self.execute("INSERT INTO tasks(id,title,state,kind,payload,created,updated) VALUES (?,?,?,?,?,?,?)", (identity, title[:200], state, kind, json.dumps(payload), now, now))
        item = self.one("SELECT * FROM tasks WHERE id=?", (identity,))
        self.event("task.changed", {"id": identity, "state": state})
        return item

    def update_task(self, identity: str, state: str, result: str = "", error: str = ""):
        self.execute("UPDATE tasks SET state=?,result=?,error=?,updated=? WHERE id=? AND state!='cancelled'", (state, result, error, time.time(), identity))
        self.event("task.changed", {"id": identity, "state": state})

    def reserve_usage(self, day: str, category: str, amount: float, limit: float):
        with self.transaction():
            self.execute("INSERT OR IGNORE INTO usage VALUES (?,?,0)", (day, category))
            current = self.one("SELECT amount FROM usage WHERE day=? AND category=?", (day, category))["amount"]
            if current + amount > limit:
                raise ValueError(f"Daily {category} limit reached")
            self.execute("UPDATE usage SET amount=amount+? WHERE day=? AND category=?", (amount, day, category))

    def capture(self, identity: str, pcm: bytes, context: dict | None = None):
        digest = hashlib.sha256(pcm).hexdigest()
        with self.transaction():
            old = self.one("SELECT digest FROM captures WHERE id=?", (identity,))
            if old and old["digest"] != digest:
                raise Conflict("Recording ID was reused with different audio")
            self.execute("INSERT OR IGNORE INTO captures(id,digest,pcm,created,context) VALUES (?,?,?,?,?)", (identity, digest, pcm, time.time(), json.dumps(context or {})))
        return self.one("SELECT id,state,transcript,result,error FROM captures WHERE id=?", (identity,))

    def close(self):
        self.db.close()
