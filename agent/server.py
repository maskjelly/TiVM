import json
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import apps, config, report, vision
from .loop import Runner

app = FastAPI(title="TiVM", version=config.VERSION)
runner = Runner()
_frame_cache = {"at": 0.0, "data": None}
TOKEN = config.TOKEN
RUN_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}$")

os.makedirs(config.RUNS_DIR, exist_ok=True)
app.mount("/runs", StaticFiles(directory=config.RUNS_DIR), name="runs")


class RunBody(BaseModel):
    tasks: list[str] | None = None
    task: str | None = None
    max_steps: int | None = None
    app: dict | None = None


class AppBody(BaseModel):
    repo: str | None = None
    local_dir: str | None = None
    ref: str | None = None
    pr: int | None = None
    dir: str | None = None
    setup: list[str] | None = None
    run: list[str] | None = None
    url: str | None = None
    ready: str | None = None


def _require_token(authorization: str | None):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="missing or invalid token")


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.post("/api/run")
def run(body: RunBody, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    tasks = body.tasks or ([body.task] if body.task else [])
    ok = runner.start(tasks, body.max_steps, app=body.app)
    if not ok:
        return JSONResponse({"ok": False, "error": "already running or no tasks"}, status_code=409)
    return {"ok": True, "run_id": runner.run_id}


@app.post("/api/prepare")
def prepare(body: AppBody, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    request = body.model_dump(exclude_none=True)
    if not request.get("repo") and not request.get("local_dir"):
        raise HTTPException(status_code=400, detail="repo or local_dir is required")
    run_dir = os.path.join(config.RUNS_DIR, "_prepare")
    os.makedirs(run_dir, exist_ok=True)
    try:
        return {"ok": True, "app": apps.prepare(request, run_dir)}
    except apps.PrepareError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)


@app.post("/api/stop")
def stop(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    runner.stop()
    return {"ok": True}


@app.get("/api/state")
def state():
    data = runner.snapshot()
    now = time.time()
    if _frame_cache["data"] is None or now - _frame_cache["at"] > 0.8:
        _frame_cache["data"] = vision.panel_frame(element=runner.last_element, label=runner.last_label)
        _frame_cache["at"] = now
    data["frame"] = _frame_cache["data"]
    return data


@app.get("/api/system")
def system():
    disk = shutil.disk_usage("/")
    mem = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                mem[key] = rest.strip()
        mem_total_kb = int(mem.get("MemTotal", "0").split()[0])
        mem_avail_kb = int(mem.get("MemAvailable", "0").split()[0])
    except OSError:
        mem_total_kb = mem_avail_kb = 0
    try:
        load = os.getloadavg()
    except OSError:
        load = (0.0, 0.0, 0.0)
    return {
        "version": config.VERSION,
        "hostname": os.uname().nodename,
        "uptime_s": round(time.monotonic()),
        "cpu_count": os.cpu_count(),
        "load": [round(x, 2) for x in load],
        "mem_total_mb": round(mem_total_kb / 1024),
        "mem_free_mb": round(mem_avail_kb / 1024),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_free_gb": round(disk.free / 1e9, 1),
        "runs_on_disk": len([d for d in os.listdir(config.RUNS_DIR) if os.path.isdir(os.path.join(config.RUNS_DIR, d))]),
    }


@app.get("/api/runs")
def runs():
    out = []
    if not os.path.isdir(config.RUNS_DIR):
        return {"runs": []}
    for name in sorted(os.listdir(config.RUNS_DIR), reverse=True):
        path = os.path.join(config.RUNS_DIR, name)
        run_json = os.path.join(path, "run.json")
        if not os.path.isdir(path) or not os.path.exists(run_json):
            continue
        try:
            with open(run_json) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": name,
                "passed": data.get("passed"),
                "duration_s": data.get("duration_s"),
                "tasks": [
                    {
                        "task": t.get("task"),
                        "passed": t.get("passed"),
                        "reason": t.get("reason"),
                        "steps": t.get("steps"),
                        "duration_s": t.get("duration_s"),
                    }
                    for t in data.get("tasks", [])
                ],
                "tokens": data.get("tokens"),
                "openai_tokens": data.get("openai_tokens"),
            }
        )
        if len(out) >= 50:
            break
    return {"runs": out}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="bad run id")
    base = os.path.join(config.RUNS_DIR, run_id)
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="no such run")
    detail = {"id": run_id, "files": [], "timeline": []}
    run_json = os.path.join(base, "run.json")
    if os.path.exists(run_json):
        with open(run_json) as fh:
            detail["summary"] = json.load(fh)
    timeline = os.path.join(base, "timeline.json")
    if os.path.exists(timeline):
        with open(timeline) as fh:
            detail["timeline"] = json.load(fh)
    for name in sorted(os.listdir(base)):
        if name.endswith((".json", ".jpg", ".png", ".log", ".txt", ".html", ".mp4")):
            detail["files"].append(
                {"name": name, "url": f"/runs/{run_id}/{name}", "bytes": os.path.getsize(os.path.join(base, name))}
            )
    return detail


@app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
def run_report(run_id: str):
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="bad run id")
    base = os.path.join(config.RUNS_DIR, run_id)
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="no such run")
    path = report.write(base)
    if not path:
        raise HTTPException(status_code=404, detail="run has no run.json yet")
    return HTMLResponse(Path(path).read_text())


@app.get("/api/result")
def result():
    return runner.result_payload()
