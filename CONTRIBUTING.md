# Contributing

## Setup

```sh
brew install colima docker docker-compose   # macOS; Linux: docker + compose plugin
make vm        # 2 vCPU / 4 GB / 40 GB VM
make up        # build the sandbox image and start it (~5 min first time)
cp .env.example .env   # OPENAI_API_KEY (default planner) and/or TYPESAFE_API_KEY
```

Panel at http://localhost:6081, desktop at http://localhost:6080/vnc.html?autoconnect=1&resize=scale.
`make status` shows the VM, container, images and disk usage. `make check` prints the toolchain
inside the sandbox. Nothing runs until you press **Run tests** (or `POST /api/run`).

## Layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The short version:

- `agent/config.py` — all questions, thresholds and model settings. **Review changes here first.**
- `agent/loop.py` — the step loop, guards, timeline, verdicts, artifact export.
- `agent/a11y.py` / `vision.py` / `actions.py` — perception and actuation primitives.
- `agent/server.py` + `agent/static/index.html` — the panel (vanilla JS, no build step).
- `docker/` — the sandbox image (Ubuntu + XFCE + noVNC + AT-SPI + toolchain).

## Conventions

- Perception produces `{source, role, text, x, y, w, h}` elements with stable ids (`e1..eN`) per
  step; element ids are the only thing the planner targets, coordinates are the fallback.
- Keep questions and thresholds in `config.py`; keep the action vocabulary in
  `openai_client.PLANNER_SYSTEM` and `config.build_questions` in sync.
- New guards must be *observable* — log them and add a timeline entry, so the panel and
  `runs/<id>/timeline.json` tell the same story.
- Every failure reason should be actionable ("blocked: auth prompt", "no progress: 4 actions
  produced no screen change"), never just "failed".

## Testing

There is no test suite in CI; testing is the product. Use the panel:

1. `make up`, open the panel, run `check toolchain` (a preset) — verifies the sandbox and the loop.
2. Run a repo task: `Clone https://github.com/owner/repo and show its files`.
3. Inspect `runs/<id>/timeline.json` and `task-N-final.jpg` when something goes wrong.

Offline checks: `python3 -m py_compile agent/*.py` (uses only stdlib).
