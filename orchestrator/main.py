import json
import mimetypes
import os
import html
import hmac
import re
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .box import Box
from .core import Worker, event_job, report_url
from .github import GitHub
from .store import Store, QueueFull

@asynccontextmanager
async def lifespan(app):
    _startup()
    try:
        yield
    finally:
        _shutdown()


app = FastAPI(title="TiVM orchestrator", version="0.5.0", lifespan=lifespan)
store = Store(config.DB_PATH)
box = Box()
github = GitHub()
worker = Worker(store, box, github)
SAFE_FILE = {".html", ".mp4", ".jpg", ".png", ".json", ".log", ".txt"}
JOBS_PER_PAGE = 50


def _startup():
    if not config.API_TOKEN:
        raise RuntimeError("TIVM_API_TOKEN is required for the hosted orchestrator")
    if not config.ALLOWED_REPOS:
        raise RuntimeError("Set TIVM_ALLOWED_REPOS to explicitly trusted owner/repo names")
    if config.CONCURRENCY != 1:
        raise RuntimeError("One dev box supports TIVM_CONCURRENCY=1 only")
    if store.recover_running():
        box.stop()
    worker.start()
    store.prune(config.KEEP_JOBS)


def _shutdown():
    worker.stop()


@app.get("/healthz")
def healthz():
    return {"ok": True, "jobs": len(store.list(5)), "box": config.BOX_URL, "public": config.PUBLIC_URL}


@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(default=""), x_github_event: str = Header(default=""), x_github_delivery: str = Header(default="")):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="webhook body too large")
    if not x_github_delivery or len(x_github_delivery) > 128:
        raise HTTPException(status_code=400, detail="missing or invalid delivery ID")
    from .github import verify_signature

    if not verify_signature(body, x_hub_signature_256, config.WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        event = json.loads(body or b"{}")
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid webhook JSON")
    event["type"] = {"issue_comment": "issue_comment", "pull_request": "pull_request"}.get(x_github_event, x_github_event)
    job, reason = event_job(event)
    if not job:
        return {"ok": True, "ignored": reason}
    _require_trusted_repo(job["repo"])
    if not config.GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GitHub authorization is not configured")
    user = job.get("actor") or ""
    permission = github.permission(job["repo"], user) if user else ""
    if permission not in ("admin", "write", "maintain"):
        return {"ok": True, "ignored": "trigger requires write permission"}
    _pin_trusted_pr(job)
    try:
        job_id, superseded, duplicate = store.enqueue_delivery(x_github_delivery, job, event)
    except QueueFull as error:
        raise HTTPException(status_code=429, detail=str(error))
    for old in superseded:
        if old["status"] == "running":
            worker.stop_job(old["id"])
    return {"ok": True, "job": job_id, "superseded": superseded, "duplicate": duplicate}


def _require_trusted_repo(repo):
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise HTTPException(status_code=400, detail="repo must be owner/name")
    if repo not in config.ALLOWED_REPOS:
        raise HTTPException(status_code=403, detail="repository is not explicitly trusted")


def _pin_trusted_pr(job):
    if not job.get("pr"):
        return
    pr = github.pull_request(job["repo"], job["pr"])
    head = (pr or {}).get("head") or {}
    if ((head.get("repo") or {}).get("full_name")) != job["repo"] or not head.get("sha"):
        raise HTTPException(status_code=403, detail="fork or unverifiable PR execution is disabled")
    job["sha"] = head["sha"]


def _require_api_token(token):
    if not config.API_TOKEN or not hmac.compare_digest(token.encode(), config.API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="missing or invalid token")


@app.get("/api/jobs")
def jobs(x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    return {"jobs": [{k: v for k, v in j.items() if k != "report_token"} for j in store.list(JOBS_PER_PAGE)]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    job["report_url"] = report_url(job)
    return job


@app.post("/api/jobs")
def create_job(body: dict, x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    repo = body.get("repo") or ""
    _require_trusted_repo(repo)
    pr = body.get("pr")
    if pr is not None and (not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0):
        raise HTTPException(status_code=400, detail="pr must be a positive integer")
    for key in ("ref", "sha", "actor"):
        value = body.get(key, "")
        if not isinstance(value, str) or len(value) > 200 or value.startswith("-"):
            raise HTTPException(status_code=400, detail=f"invalid {key}")
    pinned = {"repo": repo, "pr": pr, "sha": body.get("sha", "")}
    _pin_trusted_pr(pinned)
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        job_id = store.enqueue(
            repo, pr=pr, sha=pinned["sha"], ref=body.get("ref", ""),
            trigger="manual", actor=body.get("actor", ""),
        )
    except QueueFull as error:
        raise HTTPException(status_code=429, detail=str(error))
    return {"ok": True, "job": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] in ("queued", "running"):
        store.finish(job_id, "cancelled")
    if job["status"] == "running":
        worker.stop_job(job_id)
    return {"ok": True, "job": store.get(job_id)}


@app.get("/reports/{job_id}/{token}/{name}")
def report_file(job_id: str, token: str, name: str):
    job = store.get(job_id)
    if not job or token != job["report_token"] or not job.get("run_id"):
        raise HTTPException(status_code=404, detail="no such report")
    root = Path(config.RUNS_DIR).resolve()
    run_root = (root / job["run_id"]).resolve()
    path = (run_root / name).resolve()
    if not run_root.is_relative_to(root) or path.parent != run_root or not path.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    if path.suffix not in SAFE_FILE or not path.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    media = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(str(path), media_type=media)


@app.get("/", response_class=HTMLResponse)
def dashboard(x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    rows = []
    for raw_job in store.list(JOBS_PER_PAGE):
        job = {key: html.escape(str(value or ""), quote=True) for key, value in raw_job.items()}
        link = ""
        if job["status"] in ("done", "failed") and job.get("run_id"):
            link = f'<a href="{html.escape(report_url(raw_job), quote=True)}">report</a>'
        rows.append(
            f"<tr><td>{job['id']}</td><td>{job['repo']}</td><td>{job.get('pr') or job.get('ref') or ''}</td>"
            f"<td class=\"{job['status']}\">{job['status']}</td><td>{job.get('verdict') or ''}</td>"
            f"<td>{job.get('trigger') or ''}</td><td>{link}</td><td>{job.get('error') or ''}</td></tr>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>TiVM jobs</title>
<style>body{{font:13px ui-monospace,Menlo,monospace;background:#0b0d10;color:#e6e8eb;padding:24px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #1d2127;padding:6px 8px;text-align:left}}
a{{color:#7ab7ff}}.done{{color:#4ade80}}.failed{{color:#f87171}}.superseded,.cancelled{{color:#9aa3ad}}</style>
</head><body><h1>TiVM jobs</h1><p>box {config.BOX_URL} · public {config.PUBLIC_URL} · concurrency {config.CONCURRENCY}</p>
<table><tr><th>id</th><th>repo</th><th>pr/ref</th><th>status</th><th>verdict</th><th>trigger</th><th></th><th>error</th></tr>
{''.join(rows)}</table></body></html>"""


RULES_DIR = Path(__file__).parent / "static"
if RULES_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(RULES_DIR)), name="static")
