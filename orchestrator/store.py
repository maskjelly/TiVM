import json
import os
import secrets
import sqlite3
import threading
import time

STATES = ("queued", "running", "done", "failed", "cancelled", "superseded")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    pr INTEGER,
                    sha TEXT,
                    ref TEXT,
                    trigger TEXT,
                    actor TEXT,
                    status TEXT NOT NULL,
                    created REAL NOT NULL,
                    started REAL,
                    finished REAL,
                    run_id TEXT,
                    verdict TEXT,
                    error TEXT,
                    report_token TEXT NOT NULL,
                    comment_id INTEGER,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_key ON jobs (repo, pr, status);
                """
            )
            self.db.commit()

    def _row(self, row):
        return dict(row) if row is not None else None

    def enqueue(self, repo, pr=None, sha="", ref="", trigger="manual", actor="", payload=None):
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        with self.lock:
            self.db.execute(
                "INSERT INTO jobs (id, repo, pr, sha, ref, trigger, actor, status, created, report_token, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, repo, pr, sha, ref, trigger, actor, "queued", time.time(),
                    secrets.token_urlsafe(16), json.dumps(payload or {}),
                ),
            )
            self.db.commit()
        return job_id

    def get(self, job_id):
        with self.lock:
            return self._row(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list(self, limit=50):
        with self.lock:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def claim(self):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "UPDATE jobs SET status='running', started=? WHERE id=?", (time.time(), row["id"])
            )
            self.db.commit()
        return self.get(row["id"])

    def supersede(self, repo, pr):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, status FROM jobs WHERE repo=? AND pr IS ? AND status IN ('queued','running')",
                (repo, pr),
            ).fetchall()
            replaced = [{"id": r["id"], "status": r["status"]} for r in rows]
            for job in replaced:
                self.db.execute(
                    "UPDATE jobs SET status='superseded', finished=? WHERE id=?",
                    (time.time(), job["id"]),
                )
            self.db.commit()
        return replaced

    def finish(self, job_id, status, run_id=None, verdict=None, error=None, comment_id=None):
        if status not in STATES:
            raise ValueError(f"bad status {status}")
        with self.lock:
            self.db.execute(
                "UPDATE jobs SET status=?, finished=?, run_id=COALESCE(?, run_id),"
                " verdict=COALESCE(?, verdict), error=?, comment_id=COALESCE(?, comment_id) WHERE id=?",
                (status, time.time(), run_id, verdict, error, comment_id, job_id),
            )
            self.db.commit()
        return self.get(job_id)

    def set_comment(self, job_id, comment_id):
        with self.lock:
            self.db.execute("UPDATE jobs SET comment_id=? WHERE id=?", (comment_id, job_id))
            self.db.commit()

    def prune(self, keep):
        with self.lock:
            self.db.execute(
                "DELETE FROM jobs WHERE status IN ('done','failed','cancelled','superseded') AND id NOT IN"
                " (SELECT id FROM jobs ORDER BY created DESC LIMIT ?)",
                (keep,),
            )
            self.db.commit()
