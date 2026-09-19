import html
import json
import os
import time

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #0b0d10; color: #e6e8eb;
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0; font-weight: 600; }
h3 { font-size: 13px; margin: 18px 0 6px; text-transform: uppercase; letter-spacing: .08em; color: #9aa3ad; }
p { margin: 6px 0; }
a { color: #7ab7ff; }
.sub { color: #9aa3ad; font-size: 13px; }
.head { display: flex; align-items: flex-start; gap: 16px; justify-content: space-between;
  border-bottom: 1px solid #1d2127; padding-bottom: 16px; margin-bottom: 20px; }
.verdict { font: 700 26px/1 ui-monospace, SFMono-Regular, Menlo, monospace; padding: 10px 14px;
  border-radius: 8px; border: 1px solid #2a2f37; }
.verdict.pass { color: #4ade80; border-color: #1f5136; background: #0f1d14; }
.verdict.fail { color: #f87171; border-color: #5b2323; background: #1d0f0f; }
.verdict.none { color: #9aa3ad; }
.stats { display: flex; gap: 20px; flex-wrap: wrap; margin: 4px 0 0; }
.stats div b { display: block; font: 600 16px/1.3 ui-monospace, Menlo, monospace; }
.stats div span { color: #9aa3ad; font-size: 12px; }
.card { border: 1px solid #1d2127; border-radius: 10px; padding: 16px 18px; margin: 16px 0; background: #101317; }
.card.fail { border-color: #4a2222; }
.badge { font: 700 12px/1 ui-monospace, Menlo, monospace; padding: 5px 8px; border-radius: 5px; }
.badge.pass { background: #12351f; color: #4ade80; }
.badge.fail { background: #3a1717; color: #f87171; }
.card header { display: flex; align-items: center; gap: 12px; }
.card header .meta { margin-left: auto; color: #9aa3ad; font-size: 12px; white-space: nowrap; }
.reason { color: #d1d5db; }
video { width: 100%; margin: 10px 0; border: 1px solid #1d2127; border-radius: 8px; background: #000; }
.failure { border-left: 3px solid #7f1d1d; padding: 2px 0 2px 12px; margin: 10px 0; }
.failure dl { display: grid; grid-template-columns: 130px 1fr; gap: 2px 10px; margin: 6px 0; }
.failure dt { color: #9aa3ad; }
.failure dd { margin: 0; word-break: break-word; }
pre { background: #0b0d10; border: 1px solid #1d2127; border-radius: 6px; padding: 10px;
  overflow-x: auto; font: 12px/1.5 ui-monospace, Menlo, monospace; color: #c9d1d9; }
details { margin-top: 12px; }
summary { cursor: pointer; color: #9aa3ad; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #1d2127; vertical-align: top; }
th { color: #9aa3ad; font-weight: 600; }
td.num { color: #9aa3ad; white-space: nowrap; }
img.shot { width: 100%; border: 1px solid #1d2127; border-radius: 8px; margin-top: 8px; }
.foot { color: #6b7280; font-size: 12px; margin-top: 32px; border-top: 1px solid #1d2127; padding-top: 14px; }
"""


def _read(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _esc(value):
    if value is None or value == "":
        return ""
    return html.escape(str(value))


def _steps_by_task(timeline, index):
    steps = [e for e in timeline if e.get("kind") == "step" and e.get("task_index") == index]
    if steps or any("task_index" in e for e in timeline):
        return steps
    return [e for e in timeline if e.get("kind") == "step"] if index == 1 else []


def _step_rows(steps):
    rows = []
    for e in steps:
        target = e.get("target_text") or e.get("target") or ""
        rows.append(
            "<tr>"
            f'<td class="num">{_esc(e.get("n"))}</td>'
            f"<td>{_esc(e.get('action'))}</td>"
            f"<td>{_esc(target)}</td>"
            f"<td>{_esc(e.get('typed'))}</td>"
            f"<td>{_esc(e.get('reason'))}</td>"
            f"<td>{_esc(e.get('outcome'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _failure_block(failure):
    if not failure:
        return ""
    fields = [
        ("stage", failure.get("stage")),
        ("step", failure.get("step")),
        ("action", failure.get("action")),
        ("target", failure.get("target_text") or failure.get("target")),
        ("typed", failure.get("typed")),
        ("check", failure.get("check")),
        ("window", failure.get("focused_window")),
    ]
    rows = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in fields if v)
    screen = _esc(failure.get("screen"))
    screen_html = f"<h3>Last visible screen</h3><pre>{screen}</pre>" if screen else ""
    return (
        '<div class="failure"><h3>Where it broke</h3>'
        f"<p>{_esc(failure.get('reason'))}</p><dl>{rows}</dl></div>{screen_html}"
    )


def _video_block(video_info, index, run_files):
    if not video_info:
        return ""
    full = video_info.get("file")
    clip = video_info.get("failure_file")
    parts = []
    if clip and clip in run_files:
        parts.append(
            f'<h3>What happened before the verdict (last {int(video_info.get("failure_window", 25))}s)</h3>'
            f'<video controls preload="metadata" src="{_esc(clip)}"></video>'
        )
    if full and full in run_files:
        label = "Full flow" if not clip else "Full flow video"
        parts.append(f'<h3>{label} ({_esc(video_info.get("duration_s"))}s)</h3>'
                     f'<video controls preload="metadata" src="{_esc(full)}"></video>')
    return "".join(parts)


def _task_card(index, task, timeline, run_files):
    passed = bool(task.get("passed"))
    steps = _steps_by_task(timeline, index)
    shot = f"task-{index}-final.jpg"
    shot_html = f'<img class="shot" src="{shot}" alt="final screen">' if shot in run_files else ""
    steps_html = ""
    if steps:
        steps_html = (
            f"<details><summary>Steps ({len(steps)})</summary><table>"
            "<tr><th>#</th><th>action</th><th>target</th><th>typed</th><th>why</th><th>outcome</th></tr>"
            f"{_step_rows(steps)}</table></details>"
        )
    return (
        f'<section class="card {"pass" if passed else "fail"}"><header>'
        f'<span class="badge {"pass" if passed else "fail"}">{"PASS" if passed else "FAIL"}</span>'
        f"<h2>{_esc(task.get('task'))}</h2>"
        f'<span class="meta">{_esc(task.get("steps"))} steps · {_esc(task.get("duration_s"))}s</span>'
        "</header>"
        f'<p class="reason">{_esc(task.get("reason"))}</p>'
        f"{_video_block(task.get('video'), index, run_files)}"
        f"{_failure_block(task.get('failure'))}"
        f"{steps_html}{shot_html}</section>"
    )


def write(run_dir, name="report.html"):
    run_path = os.path.join(run_dir, "run.json")
    run = _read(run_path, None)
    if run is None:
        return None
    timeline = _read(os.path.join(run_dir, "timeline.json"), [])
    if not isinstance(timeline, list):
        timeline = []
    tasks = run.get("tasks") or []
    run_files = set(os.listdir(run_dir))
    passed_count = sum(1 for t in tasks if t.get("passed"))
    verdict = run.get("passed")
    verdict_class = "none" if verdict is None else ("pass" if verdict else "fail")
    verdict_text = "PASS" if verdict else ("FAIL" if verdict is False else "RUNNING")
    tokens = run.get("tokens") or {}
    started = run.get("started_at")
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else ""
    error = run.get("error")
    error_html = f'<p class="reason">suite error: {_esc(error)}</p>' if error else ""
    cards = "".join(_task_card(i, t, timeline, run_files) for i, t in enumerate(tasks, start=1))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TiVM report {_esc(run.get('run_id'))}</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="head"><div><h1>TiVM run report</h1>
<p class="sub">{_esc(run.get('run_id'))} · {when}</p>
<div class="stats">
<div><b>{passed_count}/{len(tasks)}</b><span>flows passed</span></div>
<div><b>{_esc(run.get('duration_s'))}s</b><span>duration</span></div>
<div><b>{_esc(tokens.get('input', 0) + tokens.get('output', 0))}</b><span>tokens</span></div>
</div></div>
<div class="verdict {verdict_class}">{verdict_text}</div></div>
{error_html}{cards}
<p class="foot">Generated by TiVM. The live desktop and control panel are internal tooling and
are not part of this report.</p>
</div></body></html>
"""
    path = os.path.join(run_dir, name)
    with open(path, "w") as fh:
        fh.write(page)
    return path
