# Contributing

## Setup

Install Docker with Compose. On macOS, install Colima, then start its VM:

```sh
brew install colima docker docker-compose
make vm
```

On macOS or Linux, configure a planner key before starting the desktop:

```sh
cp .env.example .env
# Set OPENAI_API_KEY, or set TYPESAFE_API_KEY and TIVM_PLANNER=jev.
make up
```

Panel at http://localhost:6081, desktop at http://localhost:6080/vnc.html?autoconnect=1&resize=scale.
`make status` shows the VM, container, images and disk usage. `make check` prints the toolchain
inside the sandbox. Nothing runs until you press **Run tests** (or `POST /api/run`).

`make boxes` lists containers. `make kill` removes containers and prunes images. `make nuke` also deletes the Colima VM and its disk.

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
- Failures must go through `Runner._fail(...)` and carry the structured failure context
  (stage, step, action, target, check, window, screen, elements). Every failure reason should be
  actionable ("blocked: auth prompt", "no progress: 4 actions produced no screen change"),
  never just "failed".
- A failing task must never stop the suite; remaining tasks still run.

## Validation

CI runs the Python unit suite inside the desktop image. Run it locally with `make test` after `make up`. For changes to desktop behavior, also check a full run:

1. Open the panel and run the `toolchain` preset.
2. Run the [included todo app suite](docs/DEMO.md).
3. Check the verdict, timeline, screenshot, and report in `runs/<id>/`.

Offline syntax check: `python3 -m py_compile agent/*.py` (uses only stdlib).
