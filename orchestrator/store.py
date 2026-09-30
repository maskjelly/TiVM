import json
import os
import secrets
import sqlite3
import threading
import time

STATES = ("queued", "running", "done", "failed", "cancelled", "superseded")


class QueueFull(RuntimeError):
    pass


class Store:
    def __init__(self, path, max_pending=64):
        self.max_pending = max_pending
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
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
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, created);
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created REAL NOT NULL
                );
                """
            )
            self.db.commit()

    def _check_capacity(self):
        pending = self.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if pending >= self.max_pending:
            raise QueueFull("Job queue is full; retry later")

    def _row(self, row):
        return dict(row) if row is not None else None

    def enqueue(self, repo, pr=None, sha="", ref="", trigger="manual", actor="", payload=None):
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        with self.lock:
            self._check_capacity()
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
                "UPDATE jobs SET status='running', started=? WHERE id=("
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
                ") AND status='queued' RETURNING *", (time.time(),)
            ).fetchone()
            self.db.commit()
            return self._row(row)

    def recover_running(self):
        with self.lock:
            result = self.db.execute(
                "UPDATE jobs SET status='failed', finished=?, error=? WHERE status='running'",
                (time.time(), "Orchestrator restarted; execution outcome unknown. Inspect artifacts before retrying."),
            )
            self.db.commit()
            return result.rowcount

    def enqueue_delivery(self, delivery, job, payload):
        """Deduplication, supersession and insertion share one transaction."""
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                old = self.db.execute("SELECT job_id FROM deliveries WHERE id=?", (delivery,)).fetchone()
                if old:
                    self.db.rollback()
                    return old["job_id"], [], True
                replaced = [dict(row) for row in self.db.execute(
                    "SELECT id, status FROM jobs WHERE repo=? AND pr IS ? AND status IN ('queued','running')",
                    (job["repo"], job.get("pr")),
                ).fetchall()]
                self.db.execute(
                    "UPDATE jobs SET status='superseded', finished=? WHERE repo=? AND pr IS ? AND status IN ('queued','running')",
                    (time.time(), job["repo"], job.get("pr")),
                )
                self._check_capacity()
                job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(6)
                self.db.execute(
                    "INSERT INTO jobs (id, repo, pr, sha, ref, trigger, actor, status, created, report_token, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, job["repo"], job.get("pr"), job.get("sha", ""), job.get("ref", ""),
                     job["trigger"], job.get("actor", ""), "queued", time.time(), secrets.token_urlsafe(16), json.dumps(payload)),
                )
                self.db.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery, job_id, time.time()))
                self.db.commit()
                return job_id, replaced, False
            except Exception:
                self.db.rollback()
                raise

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
                " verdict=COALESCE(?, verdict), error=?, comment_id=COALESCE(?, comment_id) WHERE id=? AND status IN ('queued','running')",
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
            self.db.execute("DELETE FROM deliveries WHERE created < ?", (time.time() - 7 * 86400,))
            self.db.execute(
                "DELETE FROM jobs WHERE status IN ('done','failed','cancelled','superseded') AND id NOT IN"
                " (SELECT id FROM jobs ORDER BY created DESC LIMIT ?)",
                (keep,),
            )
            self.db.commit()
