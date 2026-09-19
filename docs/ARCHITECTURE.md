# Architecture

```
┌─ your machine ─────────────────────────────────────────────────────────────────┐
│  colima VM (2 vCPU / 4 GB / 40 GB)          make status shows this             │
│  ┌─ docker ────────────────────────────────────────────────────────────────┐   │
│  │  dev box: tivm-desktop container (ephemeral, wiped on every start)      │   │
│  │                                                                         │   │
│  │   Xvfb :99 ──► XFCE desktop ──► x11vnc ──► websockify/noVNC  :6080      │   │
│  │                    ▲                                                    │   │
│  │                    │ pyatspi (D-Bus)         AT-SPI registry            │   │
│  │   agent loop ──────┴───────────────────────────────────────────┐       │   │
│  │   uvicorn :6081   │  perceive ─► decide ─► act ─► settle       │       │   │
│  │   (control panel) │     a11y       planner   invoke/xdotool    │       │   │
│  └───────────────────┴──────────────────────────────────────────┬─┘       │   │
│                                                                  │         │   │
│  ./agent (ro)  ── code mounted into the container                │         │   │
│  ./runs (rw)   ◄── verdicts, screenshots, timelines ─────────────┘         │   │
└────────────────────────────────────────────────────────────────────────────┘
```

## Module map (`agent/`)

| file | responsibility |
| ---- | -------------- |
| `config.py` | every question, threshold and model setting — the single file to review |
| `loop.py` | the Runner: perceive → decide → act → settle, suite execution, verdicts, timeline, artifacts |
| `a11y.py` | AT-SPI walk (roles, names, extents, focused/value), widget invocation |
| `apps.py` | `.tivm.yml` app contract, inference fallback, clone at ref/PR, setup, launch, readiness |
| `vision.py` | scrot capture, OCR fallback, pixel-diff thumbs, annotated frames |
| `video.py` | per-run screen recording (ffmpeg x11grab) and per-task clip cutting |
| `report.py` | static `report.html` from run.json + timeline + video (what PR authors see) |
| `actions.py` | xdotool wrappers (click, type, key, scroll) |
| `openai_client.py` | planner call (screenshot + plan/memory/check) and vision OCR |
| `ts.py` | TypeSafe HTTP client with retries (429/5xx) |
| `server.py` | FastAPI: control panel, run history, system stats, artifact mount |
| `static/index.html` | the panel UI (vanilla JS, no build step) |

## One step

1. **perceive** — `scrot` a frame; walk the AT-SPI tree (~50 ms). In `openai` planner mode the
   screenshot itself goes to the planner, so tesseract is skipped; in `jev` mode tesseract runs
   as a fallback when the tree is thin. Off-screen nodes are filtered.
2. **decide** — one request:
   - `openai` (default): screenshot + elements + plan/memory/check → JSON action (element id or
     x/y, free text, key, done, blocked).
   - `jev`: one TypeSafe call with `done`/`blocked`/`action`/`target` questions; `key`/`grid`
     follow-ups only when the chosen action needs them.
3. **act** — accessibility `do_action` when the widget supports it (no mouse involved), else
   `xdotool`. Typing appends Return by default. Empty targets are refused with corrective feedback.
4. **settle** — poll until the frame stops changing (min 0.35 s, up to 10 s after commands).
   Change detection is a 160×100 pixel-diff (a hash is useless — the clock ticks).

## Guards

`done` / `blocked` from the planner · 4 identical actions · 4 no-change actions · 20 consecutive
waits · Stop button. Steps are unlimited by default (`TIVM_MAX_STEPS=0`); pass `max_steps` per run
to cap a hosted job.

## Failure contract

Every failure path returns a structured object so the panel, `/api/result` and the exported JSON
all tell the same story:

```json
{
  "stage": "decide | act | settle | wait | limit | error",
  "step": 12,
  "reason": "stuck: repeated 'click' 5 times with no result",
  "action": "click",
  "target": "e10",
  "target_text": "push button \"Documents\"",
  "typed": "bun install",
  "check": "the folder is open",
  "focused_window": "root - Thunar",
  "screen": "…terminal tail…",
  "elements": ["File", "Edit", "View", "Documents", "…"]
}
```

A failed task never stops the suite: the runner logs the remaining task count and continues, and
the aggregate verdict is reported (e.g. `1/2 passing`). `Stop` and unhandled errors end the run
but still export everything captured so far.

## Environment variables

| variable | default | meaning |
| -------- | ------- | ------- |
| `OPENAI_API_KEY` | — | required for the default planner |
| `TYPESAFE_API_KEY` | — | required for `TIVM_PLANNER=jev` |
| `TIVM_PLANNER` | `openai` | `openai` or `jev` |
| `TIVM_OPENAI_PLANNER_MODEL` | `gpt-5.6-sol` | planner model |
| `TIVM_OPENAI_REASONING_EFFORT` | `none` | `none` keeps it fast and cheap |
| `TIVM_OPENAI_VISION_MODEL` | `gpt-5.6-sol` | model used by `TIVM_PERCEPTION=vision` |
| `TIVM_OPENAI_IMAGE_DETAIL` | `high` | `high` or `low` |
| `TIVM_OPENAI_MAX_ELEMENTS` | `80` | element list cap in the planner prompt |
| `TIVM_PERCEPTION` | `hybrid` | `hybrid` (a11y + tesseract for jev) · `a11y` · `vision` |
| `TIVM_A11Y_SKIP_OCR_MIN` | `10` | a11y widget count above which OCR is skipped |
| `TIVM_OCR_SCALE` | `2` | tesseract upscale factor |
| `TIVM_CHANGE_RATIO` | `0.004` | pixel-diff ratio counted as a screen change |
| `TIVM_MAX_STEPS` | `0` | 0 = unlimited |
| `TIVM_MAX_WAITS` | `20` | consecutive waits allowed before aborting |
| `TIVM_WAIT_SECONDS` | `5` | duration of one `wait` action |
| `TIVM_STALL_LIMIT` | `4` | actions with no screen change before aborting |
| `TIVM_SETTLE_MIN` / `TIVM_SETTLE_TIMEOUT` / `TIVM_COMMAND_SETTLE_TIMEOUT` | `0.35` / `1.5` / `10` | settle timings in seconds |
| `TIVM_TYPE_RETURN` | `1` | press Return after typing |
| `TIVM_BLOCKED_THRESHOLD` / `TIVM_MIN_CONFIDENCE` | `0.70` / `0.35` | Jev planner thresholds |
| `TIVM_TOKEN` | empty | if set, mutating endpoints require `Authorization: Bearer <token>` |
| `TIVM_KEEP_RUNS` | `20` | artifact folders kept on disk |
| `TIVM_VIDEO` | `1` | record the desktop with ffmpeg (needs ffmpeg in the image) |
| `TIVM_VIDEO_FPS` / `TIVM_VIDEO_CRF` | `10` / `30` | capture rate and quality |
| `TIVM_VIDEO_FAILURE_WINDOW` | `25` | seconds kept in `task-N-failure.mp4` |
| `TIVM_BIND` | `0.0.0.0` | host bind address for 6080/6081; hosted deploys use `127.0.0.1` |
| `TIVM_RUNS_DIR` | `/app/runs` | artifact directory inside the container |
| `TIVM_PROJECTS_DIR` | `/root/projects` | where repos are cloned for the prepare phase |
| `SCREEN_W` / `SCREEN_H` | `1280` / `800` | desktop resolution |

## HTTP API

| method | path | purpose |
| ------ | ---- | ------- |
| `POST` | `/api/run` | `{tasks: [...], max_steps?, app?}` → start a suite; with `app` the repo is prepared first, and with no `tasks` the contract's flows are the suite |
| `POST` | `/api/prepare` | `{repo, ref?\|pr?, dir?, run?, url?, ...}` → clone, setup, launch and wait for readiness; no model calls |
| `POST` | `/api/stop` | stop after the current action |
| `GET` | `/api/state` | live state: status, timeline, plan, frame, tokens |
| `GET` | `/api/system` | version, uptime, CPU, memory, disk, runs on disk |
| `GET` | `/api/runs` | completed runs (id, verdict, duration, tokens) |
| `GET` | `/api/runs/{id}` | run summary, timeline, artifact file list |
| `GET` | `/api/result` | verdict payload of the current/last run |
| `GET` | `/api/runs/{id}/report` | build/return the static report page |
| `GET` | `/runs/{id}/...` | artifact files (final screenshots, JSON) |

## Persistence

The container filesystem is wiped at every start (entrypoint) and destroyed on recreate;
`./runs` (artifacts) and `./agent` (code) are the only host mounts. See the README table.
