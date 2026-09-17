from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import vision
from .loop import Runner

app = FastAPI(title="TiVM")
runner = Runner()
_frame_cache = {"at": 0.0, "data": None}


class RunBody(BaseModel):
    tasks: list[str] | None = None
    task: str | None = None
    max_steps: int | None = None


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.post("/api/run")
def run(body: RunBody):
    tasks = body.tasks or ([body.task] if body.task else [])
    ok = runner.start(tasks, body.max_steps)
    if not ok:
        return JSONResponse({"ok": False, "error": "already running or no tasks"}, status_code=409)
    return {"ok": True}


@app.post("/api/stop")
def stop():
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


@app.get("/api/result")
def result():
    return runner.result_payload()
