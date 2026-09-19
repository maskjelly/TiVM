import json
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .box import Box
from .core import Worker, event_job, report_url
from .github import GitHub
from .store import Store

app = FastAPI(title="TiVM orchestrator", version="0.5.0")
store = Store(config.DB_PATH)
box = Box()
github = GitHub()
worker = Worker(store, box, github)
SAFE_FILE = {".html", ".mp4", ".jpg", ".png", ".json", ".log", ".txt"}
JOBS_PER_PAGE = 50


@app.on_event("startup")
def _startup():
    worker.start()
    store.prune(config.KEEP_JOBS)


@app.get("/healthz")
def healthz():
    return {"ok": True, "jobs": len(store.list(5)), "box": config.BOX_URL, "public": config.PUBLIC_URL}


@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(default=""), x_github_event: str = Header(default="")):
    body = await request.body()
    from .github import verify_signature

    if not verify_signature(body, x_hub_signature_256, config.WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="bad signature")
    event = json.loads(body or b"{}")
    event["type"] = {"issue_comment": "issue_comment", "pull_request": "pull_request"}.get(x_github_event, x_github_event)
    job, reason = event_job(event)
    if not job:
        return {"ok": True, "ignored": reason}
    if config.GITHUB_TOKEN:
        user = job.get("actor") or ""
        permission = github.permission(job["repo"], user) if user else ""
        if permission not in ("admin", "write", "triage"):
            return {"ok": True, "ignored": f"{user} has no write permission ({permission or 'unknown'})"}
    superseded = store.supersede(job["repo"], job.get("pr"))
    if any(old["status"] == "running" for old in superseded):
        box.stop()
    job_id = store.enqueue(
        job["repo"], pr=job.get("pr"), sha=job.get("sha", ""), ref=job.get("ref", ""),
        trigger=job["trigger"], actor=job.get("actor", ""), payload=event,
    )
    return {"ok": True, "job": job_id, "superseded": superseded}


def _require_api_token(token):
    if config.API_TOKEN and token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="missing or invalid token")


@app.get("/api/jobs")
def jobs():
    return {"jobs": [{k: v for k, v in j.items() if k != "report_token"} for j in store.list(JOBS_PER_PAGE)]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    job["report_url"] = report_url(job)
    return job


@app.post("/api/jobs")
def create_job(body: dict, x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    repo = (body.get("repo") or "").strip()
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    job_id = store.enqueue(
        repo, pr=body.get("pr"), sha=body.get("sha", ""), ref=body.get("ref", ""),
        trigger="manual", actor=body.get("actor", ""),
    )
    return {"ok": True, "job": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, x_tivm_token: str = Header(default="")):
    _require_api_token(x_tivm_token)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] == "running":
        box.stop()
    if job["status"] in ("queued", "running"):
        store.finish(job_id, "cancelled")
    return {"ok": True, "job": store.get(job_id)}


@app.get("/reports/{job_id}/{token}/{name}")
def report_file(job_id: str, token: str, name: str):
    job = store.get(job_id)
    if not job or token != job["report_token"] or not job.get("run_id"):
        raise HTTPException(status_code=404, detail="no such report")
    if name not in os.listdir(str(Path(config.RUNS_DIR) / job["run_id"])):
        raise HTTPException(status_code=404, detail="no such file")
    path = Path(config.RUNS_DIR) / job["run_id"] / name
    if path.suffix not in SAFE_FILE or not path.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    media = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(str(path), media_type=media)


@app.get("/", response_class=HTMLResponse)
def dashboard():
    rows = []
    for job in store.list(JOBS_PER_PAGE):
        link = ""
        if job["status"] in ("done", "failed") and job.get("run_id"):
            link = f'<a href="{report_url(job)}">report</a>'
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
