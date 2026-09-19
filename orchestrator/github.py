import hashlib
import hmac
import time

import requests

from . import config


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


class GitHub:
    """Thin API client. Without a token every call is skipped and recorded."""

    MARKER = "<!-- tivm-report -->"
    CHECK_NAME = "TiVM customer flows"

    def __init__(self, token="", api=None, session=None, log=print):
        self.token = token or config.GITHUB_TOKEN
        self.api = (api or config.GITHUB_API).rstrip("/")
        self.session = session or requests.Session()
        self.log = log
        self.calls = []

    def _request(self, method, path, **kwargs):
        url = path if path.startswith("http") else f"{self.api}{path}"
        self.calls.append((method, url))
        if not self.token:
            self.log(f"github: skipped {method} {url} (no token)")
            return None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = self.session.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            self.log(f"github: {method} {url} -> HTTP {response.status_code}: {response.text[:200]}")
            return None
        return response.json() if response.content else {}

    def permission(self, repo, user):
        if not self.token:
            return "write"
        data = self._request("GET", f"/repos/{repo}/collaborators/{user}/permission")
        if not data:
            return ""
        return data.get("permission", "")

    def upsert_comment(self, repo, pr, body):
        comments = self._request("GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100") or []
        for comment in comments:
            if self.MARKER in (comment.get("body") or ""):
                updated = self._request("PATCH", f"/repos/{repo}/issues/comments/{comment['id']}", json={"body": body})
                if updated:
                    return comment["id"]
        created = self._request("POST", f"/repos/{repo}/issues/{pr}/comments", json={"body": body})
        return created.get("id") if created else None

    def report_check(self, repo, sha, conclusion, title, summary, url):
        if not sha:
            return None
        payload = {
            "name": self.CHECK_NAME,
            "head_sha": sha,
            "status": "completed",
            "conclusion": conclusion,
            "details_url": url,
            "output": {"title": title[:255], "summary": summary[:65000]},
        }
        data = self._request("POST", f"/repos/{repo}/check-runs", json=payload)
        return data.get("id") if data else None


def build_comment(job, result, report_url, box_public=""):
    tasks = result.get("tasks") or []
    passed = sum(1 for t in tasks if t.get("passed"))
    verdict = "PASS" if result.get("passed") else "FAIL"
    duration = result.get("duration_s")
    lines = [
        GitHub.MARKER,
        f"**TiVM customer flows — {verdict}** · {passed}/{len(tasks)} passed"
        + (f" · {duration}s" if duration is not None else "")
        + f" · [full report]({report_url})",
    ]
    prepare_error = result.get("prepare_error")
    if prepare_error:
        lines += ["", f"> Could not prepare the app: `{prepare_error}`"]
    elif tasks:
        lines += ["", "| flow | result | steps | time | video |", "| --- | --- | --- | --- | --- |"]
        for index, task in enumerate(tasks, start=1):
            video = task.get("video") or {}
            clip = video.get("failure_file") or video.get("file")
            link = f"[watch]({report_url.rsplit('/', 1)[0]}/{clip})" if clip else ""
            name = str(task.get("task") or "")[:70].replace("|", "\\|")
            lines.append(
                f"| {name} | {'pass' if task.get('passed') else '**fail**'} | {task.get('steps')} "
                f"| {task.get('duration_s')}s | {link} |"
            )
    failures = [t for t in tasks if not t.get("passed")]
    for index, task in enumerate(tasks, start=1):
        if task.get("passed") or not task.get("failure"):
            continue
        failure = task["failure"]
        lines += [
            "",
            f"**Failed: {str(task.get('task'))[:120]}**",
            f"`{failure.get('stage')}` at step {failure.get('step')}: {failure.get('reason')}",
        ]
        if failure.get("check"):
            lines.append(f"> was checking: {failure.get('check')}")
        shot = f"{report_url.rsplit('/', 1)[0]}/task-{index}-final.jpg"
        lines.append(f"![final screen]({shot})")
    lines += [
        "",
        f"<sub>run `{job.get('run_id') or 'n/a'}` · trigger: {job.get('trigger')} · "
        f"this comment updates on every push{'' if box_public else ''}</sub>",
    ]
    return "\n".join(lines)
