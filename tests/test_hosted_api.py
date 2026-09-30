import hashlib
import hmac
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator import config
from orchestrator.store import Store

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipUnless(TestClient, "Install orchestrator requirements and httpx for hosted API tests")
class HostedApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tivm-api-"))
        self.patches = [patch.object(config, "DB_PATH", str(self.root / "jobs.sqlite")),
                        patch.object(config, "API_TOKEN", "test-token"),
                        patch.object(config, "ALLOWED_REPOS", frozenset({"owner/name"})),
                        patch.object(config, "WEBHOOK_SECRET", "test-secret"),
                        patch.object(config, "GITHUB_TOKEN", "test-github"),
                        patch.object(config, "RUNS_DIR", str(self.root / "runs"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.main = importlib.import_module("orchestrator.main")
        self.store = Store(str(self.root / "case.sqlite"))
        self.patch_store = patch.object(self.main, "store", self.store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.client = TestClient(self.main.app)
        self.headers = {"X-TiVM-Token": "test-token"}

    def test_job_metadata_and_dashboard_require_auth(self):
        job = self.store.enqueue("owner/name")
        for url in ("/api/jobs", "/api/jobs/" + job, "/"):
            self.assertEqual(self.client.get(url).status_code, 401)
            self.assertEqual(self.client.get(url, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_dashboard_escapes_job_data(self):
        self.store.enqueue("<script>alert(1)</script>", ref='<img src=x onerror="alert(1)">')
        text = self.client.get("/", headers=self.headers).text
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<img", text)

    def test_manual_intake_rejects_untrusted_repos_and_malformed_inputs(self):
        for body, expected in [({"repo": "other/repo"}, 403), ({"repo": "https://evil.example/repo"}, 400),
                               ({"repo": []}, 400), ({"repo": "owner/name", "pr": True}, 400),
                               ({"repo": "owner/name", "ref": "--upload-pack=evil"}, 400)]:
            self.assertEqual(self.client.post("/api/jobs", json=body, headers=self.headers).status_code, expected)
        response = self.client.post("/api/jobs", json={"repo": "owner/name"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_webhooks_deduplicate_and_reject_forks(self):
        event = {"action": "labeled", "number": 7, "label": {"name": "tivm"},
                 "repository": {"full_name": "owner/name"}, "sender": {"login": "writer"},
                 "pull_request": {"user": {"login": "author"}, "head": {"sha": "a" * 40}}}
        body = json.dumps(event).encode()
        headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-1",
                   "X-Hub-Signature-256": "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()}
        with patch.object(self.main.github, "permission", return_value="write"), patch.object(
            self.main.github, "pull_request", return_value={"head": {"repo": {"full_name": "owner/name"}, "sha": "a" * 40}}
        ):
            first = self.client.post("/webhook", content=body, headers=headers)
            second = self.client.post("/webhook", content=body, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["job"], second.json()["job"])
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(len(self.store.list()), 1)
        with patch.object(self.main.github, "permission", return_value="write"), patch.object(
            self.main.github, "pull_request", return_value={"head": {"repo": {"full_name": "fork/name"}, "sha": "a" * 40}}
        ):
            headers["X-GitHub-Delivery"] = "delivery-2"
            self.assertEqual(self.client.post("/webhook", content=body, headers=headers).status_code, 403)

    def test_report_cannot_serve_a_symlink_outside_its_run(self):
        run = self.root / "runs/run1"
        run.mkdir(parents=True)
        secret = self.root / "private.txt"
        secret.write_text("private")
        (run / "leak.txt").symlink_to(secret)
        job_id = self.store.enqueue("owner/name")
        job = self.store.finish(job_id, "done", run_id="run1")
        response = self.client.get(f'/reports/{job_id}/{job["report_token"]}/leak.txt')
        self.assertEqual(response.status_code, 404)
