import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import core
from orchestrator.github import GitHub, build_comment, verify_signature
from orchestrator.store import Store


def event(kind, action, **extra):
    base = {"type": kind, "action": action, "repository": {"full_name": "owner/name"}}
    base.update(extra)
    return base


def issue_comment_event(body):
    return event(
        "issue_comment", "created",
        issue={"number": 7, "pull_request": {"head": {"sha": "abc"}}},
        comment={"body": body, "user": {"login": "octo"}},
    )


def pr_event(action, labels=("tivm",), number=7):
    return event(
        "pull_request", action, number=number,
        pull_request={"head": {"sha": "abc"}, "user": {"login": "octo"}, "labels": [{"name": l} for l in labels]},
        label={"name": labels[0]} if action == "labeled" and labels else None,
    )


class TriggerTests(unittest.TestCase):
    def test_comment_with_mention_enqueues(self):
        job, reason = core.event_job(issue_comment_event("hey @tivm test this please"))
        self.assertEqual(reason, "")
        self.assertEqual(job["repo"], "owner/name")
        self.assertEqual(job["pr"], 7)
        self.assertEqual(job["trigger"], "comment")

    def test_comment_without_mention_is_ignored(self):
        job, reason = core.event_job(issue_comment_event("nice work"))
        self.assertIsNone(job)
        self.assertIn("mention", reason)

    def test_comment_on_plain_issue_is_ignored(self):
        payload = event("issue_comment", "created", issue={"number": 1, "pull_request": None},
                        comment={"body": "@tivm go", "user": {"login": "octo"}})
        job, reason = core.event_job(payload)
        self.assertIsNone(job)
        self.assertIn("pull request", reason)

    def test_pr_labeled_with_trigger_label_enqueues(self):
        job, reason = core.event_job(pr_event("labeled"))
        self.assertEqual(reason, "")
        self.assertEqual(job["ref"], "pull/7/head")
        self.assertEqual(job["trigger"], "pr:labeled")

    def test_pr_labeled_with_other_label_is_ignored(self):
        job, reason = core.event_job(pr_event("labeled", labels=("bug",)))
        self.assertIsNone(job)

    def test_pr_opened_without_label_ignored_by_default(self):
        job, reason = core.event_job(pr_event("opened", labels=()))
        self.assertIsNone(job)
        self.assertIn("label", reason)

    def test_signature_round_trip(self):
        body = json.dumps({"type": "ping"}).encode()
        good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, good, "secret"))
        self.assertFalse(verify_signature(body, good, "other"))
        self.assertFalse(verify_signature(body, "", "secret"))
        self.assertFalse(verify_signature(body, "sha256=deadbeef", "secret"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="tivm-store-"), "jobs.sqlite")
        self.store = Store(self.path)

    def test_enqueue_claim_finish(self):
        job_id = self.store.enqueue("owner/name", pr=3, trigger="comment")
        self.assertEqual(self.store.get(job_id)["status"], "queued")
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["status"], "running")
        self.store.finish(job_id, "done", run_id="run1", verdict="pass", comment_id=42)
        job = self.store.get(job_id)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["run_id"], "run1")
        self.assertEqual(job["comment_id"], 42)
        self.assertIsNone(self.store.claim())

    def test_supersede_replaces_previous_jobs_for_the_same_pr(self):
        first = self.store.enqueue("owner/name", pr=3)
        self.store.claim()
        second = self.store.enqueue("owner/name", pr=3)
        replaced = self.store.supersede("owner/name", 3)
        self.assertCountEqual([r["id"] for r in replaced], [first, second])
        self.assertEqual([r["status"] for r in replaced if r["id"] == first], ["running"])
        self.assertEqual(self.store.get(first)["status"], "superseded")
        self.assertEqual(self.store.get(second)["status"], "superseded")

    def test_supersede_ignores_other_prs(self):
        keep = self.store.enqueue("owner/name", pr=4)
        self.store.enqueue("owner/name", pr=3)
        self.assertEqual(self.store.supersede("owner/name", 3) != [], True)
        self.assertEqual(self.store.get(keep)["status"], "queued")

    def test_report_token_is_unique_and_present(self):
        a = self.store.get(self.store.enqueue("a/b", pr=1))
        b = self.store.get(self.store.enqueue("a/b", pr=2))
        self.assertTrue(a["report_token"] and b["report_token"])
        self.assertNotEqual(a["report_token"], b["report_token"])


class CommentTests(unittest.TestCase):
    def _job(self):
        return {"id": "job1", "run_id": "run1", "trigger": "comment"}

    def _result(self):
        return {
            "passed": False, "duration_s": 42.0,
            "tasks": [
                {"task": "add a todo", "passed": True, "steps": 5, "duration_s": 20.0,
                 "video": {"file": "task-1.mp4"}},
                {"task": "complete a todo", "passed": False, "steps": 4, "duration_s": 22.0,
                 "video": {"file": "task-2.mp4", "failure_file": "task-2-failure.mp4"},
                 "failure": {"stage": "settle", "step": 4, "reason": "no progress", "check": "item is done"}},
            ],
        }

    def test_comment_has_marker_table_and_failure_links(self):
        url = "https://tivm.example/reports/job1/tok123/report.html"
        body = build_comment(self._job(), self._result(), url)
        self.assertIn(GitHub.MARKER, body)
        self.assertIn("FAIL", body)
        self.assertIn("1/2 passed", body)
        self.assertIn(url, body)
        self.assertIn("https://tivm.example/reports/job1/tok123/task-2-failure.mp4", body)
        self.assertIn("https://tivm.example/reports/job1/tok123/task-2-final.jpg", body)
        self.assertIn("no progress", body)

    def test_comment_reports_prepare_failures(self):
        result = {"passed": False, "tasks": [], "prepare_error": "setup failed: bun install"}
        body = build_comment(self._job(), result, "https://x/reports/j/r/report.html")
        self.assertIn("Could not prepare the app", body)
        self.assertIn("bun install", body)


class FakeBox:
    def __init__(self, result, state_ok=True):
        self.result_value = result
        self.started = []
        self.stopped = 0
        self.state_ok = state_ok

    def start(self, app, tasks=None, max_steps=None):
        self.started.append((app, tasks, max_steps))
        return "run-42"

    def wait(self, run_id, timeout=None, poll=10):
        return self.state_ok

    def result(self):
        return self.result_value

    def stop(self):
        self.stopped += 1


class FakeGitHub:
    def __init__(self):
        self.comments = []
        self.checks = []

    def upsert_comment(self, repo, pr, body):
        self.comments.append((repo, pr, body))
        return 99

    def report_check(self, repo, sha, conclusion, title, summary, url):
        self.checks.append((repo, sha, conclusion, title))
        return 1

    def permission(self, repo, user):
        return "write"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(prefix="tivm-worker-"), "jobs.sqlite"))
        self.box = FakeBox({"passed": False, "run_id": "run-42", "duration_s": 9.0, "tasks": [
            {"task": "flow", "passed": False, "steps": 2, "duration_s": 9.0,
             "failure": {"stage": "decide", "step": 2, "reason": "blocked"}},
        ]})
        self.github = FakeGitHub()
        self.worker = core.Worker(self.store, self.box, self.github, runs_dir="/tmp", log=lambda *_: None)

    def test_worker_runs_a_job_and_publishes(self):
        job_id = self.store.enqueue("owner/name", pr=7, sha="abc", trigger="comment", actor="octo")
        self.worker._run(self.store.get(job_id))
        job = self.store.get(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["verdict"], "fail")
        self.assertEqual(job["run_id"], "run-42")
        self.assertEqual(len(self.github.comments), 1)
        self.assertEqual(self.github.comments[0][1], 7)
        self.assertEqual(self.github.checks[0][2], "failure")
        self.assertEqual(self.box.started[0][0], {"repo": "owner/name", "pr": 7})

    def test_superseded_job_does_not_publish(self):
        job_id = self.store.enqueue("owner/name", pr=7, trigger="comment")
        self.store.supersede("owner/name", 7)
        self.worker._run(self.store.get(job_id))
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.checks, [])

    def test_timeout_stops_the_box(self):
        self.box.state_ok = False
        job_id = self.store.enqueue("owner/name", pr=7, sha="abc", trigger="comment")
        self.worker._run(self.store.get(job_id))
        self.assertEqual(self.store.get(job_id)["status"], "failed")
        self.assertEqual(self.box.stopped, 1)
        self.assertEqual(self.github.checks[0][2], "failure")


if __name__ == "__main__":
    unittest.main()
