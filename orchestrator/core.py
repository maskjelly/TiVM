import threading
import time

from . import config
from .box import BoxError
from .github import GitHub, build_comment


def event_job(event, delivery=""):
    """GitHub webhook event -> job dict, or (None, reason)."""
    kind = event.get("type") or ""
    action = event.get("action") or ""
    repo = (event.get("repository") or {}).get("full_name") or ""
    if not repo:
        return None, "no repository in event"

    if kind == "issue_comment":
        if action != "created":
            return None, f"issue_comment action {action}"
        issue = event.get("issue") or {}
        if not issue.get("pull_request"):
            return None, "comment is not on a pull request"
        body = (event.get("comment") or {}).get("body") or ""
        if config.TRIGGER_MENTION not in body.lower():
            return None, "comment does not mention the bot"
        return {
            "repo": repo,
            "pr": issue.get("number"),
            "sha": (issue.get("pull_request") or {}).get("head", {}).get("sha", ""),
            "ref": "",
            "trigger": "comment",
            "actor": ((event.get("comment") or {}).get("user") or {}).get("login", ""),
        }, ""

    if kind == "pull_request":
        pr = event.get("pull_request") or {}
        if action not in ("opened", "synchronize", "reopened", "labeled"):
            return None, f"pull_request action {action}"
        if action == "labeled":
            label = ((event.get("label") or {}).get("name") or "").lower()
            if label != config.TRIGGER_LABEL:
                return None, f"label {label} is not the trigger label"
        elif not config.RUN_ON_OPEN:
            return None, f"pull_request {action} without the trigger label (TIVM_RUN_ON_OPEN=0)"
        elif action in ("opened", "synchronize", "reopened"):
            if not any((l.get("name") or "").lower() == config.TRIGGER_LABEL for l in pr.get("labels") or []):
                return None, "pull request has no trigger label"
        return {
            "repo": repo,
            "pr": event.get("number"),
            "sha": (pr.get("head") or {}).get("sha", ""),
            "ref": f"pull/{event.get('number')}/head",
            "trigger": f"pr:{action}",
            "actor": ((pr.get("user") or {}).get("login", "")),
        }, ""

    return None, f"ignored event {kind or 'unknown'}"


def report_url(job, name="report.html"):
    return f"{config.PUBLIC_URL}/reports/{job['id']}/{job['report_token']}/{name}"


class Worker:
    def __init__(self, store, box, github=None, runs_dir=None, log=print):
        self.store = store
        self.box = box
        self.github = github if github is not None else GitHub(log=log)
        self.runs_dir = runs_dir or config.RUNS_DIR
        self.log = log
        self.stop_flag = False
        self.threads = []

    def start(self):
        for i in range(max(1, config.CONCURRENCY)):
            thread = threading.Thread(target=self._loop, name=f"tivm-worker-{i}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_flag = True

    def _loop(self):
        while not self.stop_flag:
            job = self.store.claim()
            if job is None:
                time.sleep(3)
                continue
            try:
                self._run(job)
            except Exception as e:
                self.log(f"job {job['id']} crashed: {e}")
                self.store.finish(job["id"], "failed", error=str(e)[:500])

    def _run(self, job):
        current = self.store.get(job["id"])
        if current and current["status"] == "superseded":
            return
        app = {"repo": job["repo"]}
        if job.get("pr"):
            app["pr"] = job["pr"]
        else:
            app["ref"] = job.get("ref") or None
        self.log(f"job {job['id']}: run {job['repo']}#{job.get('pr') or job.get('ref')}")
        try:
            run_id = self.box.start(app)
        except BoxError as e:
            self.store.finish(job["id"], "failed", error=f"box: {e}")
            self._publish_check(job, "failure", "Could not start the run", str(e))
            return
        finished = self.box.wait(run_id)
        if not finished:
            self.box.stop()
            self.store.finish(job["id"], "failed", run_id=run_id, error="run timed out")
            self._publish_check(job, "failure", "Run timed out", f"After {config.JOB_TIMEOUT}s")
            return
        result = self.box.result()
        passed = bool(result.get("passed"))
        if self.store.get(job["id"])["status"] == "superseded":
            self.log(f"job {job['id']}: superseded, not publishing")
            return
        url = report_url(job)
        comment_id = None
        if job.get("pr"):
            body = build_comment(job, result, url)
            comment_id = self.github.upsert_comment(job["repo"], job["pr"], body)
        summary = self._summary(result)
        self._publish_check(job, "success" if passed else "failure", summary["title"], summary["text"])
        self.store.finish(
            job["id"], "done" if passed else "failed",
            run_id=run_id, verdict="pass" if passed else "fail", comment_id=comment_id,
        )
        self.log(f"job {job['id']}: {'pass' if passed else 'fail'} (run {run_id})")

    def _summary(self, result):
        tasks = result.get("tasks") or []
        passed = sum(1 for t in tasks if t.get("passed"))
        title = f"{passed}/{len(tasks)} flows passed"
        if result.get("prepare_error"):
            title = "app prepare failed"
        lines = [f"report: {result.get('run_id')}"]
        for task in tasks:
            lines.append(f"- {'pass' if task.get('passed') else 'fail'}: {task.get('task')} ({task.get('reason')})")
        return {"title": title, "text": "\n".join(lines)[:65000]}

    def _publish_check(self, job, conclusion, title, text):
        if not job.get("sha"):
            return
        self.github.report_check(job["repo"], job["sha"], conclusion, title, text, report_url(job))
