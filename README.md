# TiVM

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#quick-start)
[![Decisions](https://img.shields.io/badge/decisions-TypeSafe%20jev%20%7C%20OpenAI-8b5cf6.svg)](https://docs.typesafe.ai)

**Computer-use agent for a throwaway Linux desktop.** Give it a task in plain English — it opens
apps, types commands, installs repos, runs dev servers and clicks through the UI. The screen is
read through the **AT-SPI accessibility tree** (not pixels), decisions come from either
**TypeSafe Jev** typed questions or a **GPT-5.6** planner that can write shell commands, and
actions run through a11y `invoke` or `xdotool`. A fresh sandbox on every run; verdicts and
screenshots are exported before teardown.

![Control panel](docs/panel.png)
*Control panel: live screen with the chosen target boxed, the orchestrator's plan/memory strip,
per-step timeline (action, reason, self-check, timings, tokens) and per-task verdicts.*

## Quick start

```sh
brew install colima docker docker-compose     # or Docker Desktop / your distro's docker
make vm && make up                            # 2 vCPU / 4 GB / 40 GB VM + sandbox image (~5 min)
cp .env.example .env                          # add OPENAI_API_KEY and/or TYPESAFE_API_KEY
```

- **Control panel** — http://localhost:6081 (type tasks, watch every decision)
- **Desktop (noVNC)** — http://localhost:6080/vnc.html?autoconnect=1&resize=scale

Type one task per line in the panel and hit **Run tests**; each line is a test case with its own
PASS/FAIL verdict. Nothing runs until you trigger it. Stop with `make down`.

Notes: the TypeSafe org behind `TYPESAFE_API_KEY` needs credits (otherwise every Jev step returns
HTTP 402). `OPENAI_API_KEY` is only needed for the default OpenAI planner.

## Perception: the screen becomes text

| source | cost | what it gives |
| ------ | ---- | ------------- |
| **AT-SPI accessibility tree** (primary) | ~50ms | role + name + coordinates for real widgets; clicks can be *invoked on the widget* instead of moving the mouse |
| **tesseract OCR**, 2x upscale (fallback) | ~400-700ms | text on surfaces a11y can't see (canvas, web content, non-GTK apps); runs only when a11y coverage is thin |
| **OpenAI vision OCR** (`TIVM_PERCEPTION=vision`) | ~2s | optional semantic OCR when you want no local dependencies |

Off-screen a11y nodes are filtered (Firefox happily reports links 6,000px below the viewport),
submenus are exposed as role `menu` vs `menu item`, and focused-but-unlabeled widgets show up as
`(focused, no label)` so the planner knows where typing will land. A 160x100 pixel-diff thumb
drives change detection and the "did that do anything" check.

## One step

```
perceive (a11y + optional OCR) → one planner call → act → settle
```

### Planners (`TIVM_PLANNER`)

| mode | model | sees pixels | generates text | measured per step |
| ---- | ----- | ----------- | -------------- | ----------------- |
| `openai` (default) | `TIVM_OPENAI_PLANNER_MODEL`, default `gpt-5.6-sol` with `reasoning=none` | yes — screenshot in the same call | yes (any shell command, search query, message) | ~2.0–2.7s, ~2.6k tokens |
| `jev` | TypeSafe `jev-latest` | no — text state only | no (types only text found in the task) | ~0.9s decide, ~1.5k in / 0.25k out |

The planner is an orchestrator with memory: each reply carries a `plan` (remaining substeps),
a `memory` string (repo path, dev-server port, what is still installing) and a `check` (what must
be true after the action, verified on the next step). Steps are unlimited by default
(`TIVM_MAX_STEPS=0`); a run ends on `done`, `blocked`, a stall, or the Stop button.

Measured on the demo task (open the text editor, type `"hello from Jev"`):

- OpenAI planner: 5 steps, 18.4s wall, ~2.6k tokens/step (free text typed by the model)
- Jev planner: 6 steps, 10.3s wall, ~1.5k in / 0.25k out per step


## What's in the sandbox

A throwaway Ubuntu 24.04 dev box: `apt` (with package lists kept, so `apt-get install` works
in-session), `git`, Python 3 + venv + pip, Node 24 + npm, Bun, Firefox (Mozilla build, a11y
visible; its one-time privacy notice is dismissed by the agent on first launch), Epiphany, `gcc`/`make`/`pkg-config`/`libssl-dev`,
`curl`/`wget`/`git`/`jq`/`unzip`/`sudo`, XFCE desktop, tesseract, xdotool, at-spi. `make check`
prints every version.

## How it works

```
make up ──► colima VM + Docker ──► tivm-desktop container ──► XFCE desktop (noVNC :6080)
                                               │
                                               └─ uvicorn control panel (:6081)
```

**Nothing runs on its own.** The container boots a desktop and an idle control panel. A task runs
only when triggered:

| trigger | what happens |
| ------- | ------------ |
| panel → **Run tests** | `POST /api/run {tasks: [...]}` on :6081, one task per line |
| `curl -X POST localhost:6081/api/run -d '{"tasks":["..."]}'` | same, scriptable/CI |
| `POST /api/stop` | stops after the current action |

Each task: perceive (a11y) → one OpenAI planner call (screenshot + plan/memory/check) → act
(invoke or xdotool) → settle. The planner keeps a running `plan` and `memory` across steps so it
stays on task. Verdict per task: `PASS`/`FAIL` with a reason, at `/api/result` and in the panel.

Artifacts are exported to the host as the run goes:

```
runs/<timestamp>/run.json            suite summary
runs/<timestamp>/task-N.json         verdict, steps, tokens, duration
runs/<timestamp>/task-N-final.jpg    annotated final screen
runs/<timestamp>/timeline.json       every step (action, reason, timings, tokens)
```

`make runs` lists them, `make wipe-runs` deletes them, `TIVM_KEEP_RUNS` (default 20) auto-prunes.

Changes are committed and pushed to `origin/main` (`git push`), never from inside the container —
the only host-mounted paths are `./agent` (read-only code) and `./runs` (artifacts out).

## What persists (and what is wiped)

The sandbox is ephemeral by design: every `make up` starts a fresh machine.

| thing | where | survives teardown? |
| ----- | ----- | ------------------ |
| run artifacts (`run.json`, per-task verdicts, final screenshots, timeline) | `./runs/<timestamp>/` on the host, mounted into the container | **yes** |
| cloned repos, `bun install`/`npm install`, apt packages, caches, browser profile | container filesystem | no — wiped at every container start by the entrypoint, and thrown away when the container is recreated |
| the image (git, node, bun, python, firefox, tesseract) | VM disk (`~/.colima/_lima`) | yes, until `colima delete` or an image prune |

Commands:

```sh
make vm          # one-time: colima VM, 2 vCPU / 4 GB RAM / 40 GB disk (docker storage on it)
make up          # always starts fresh: down + prune dangling layers + recreate
make runs        # list exported run folders
make wipe-runs   # delete all artifacts
make clean       # down + remove dangling images and build cache (frees GBs)
```

`TIVM_KEEP_RUNS` (default 20) caps how many run folders are kept; older ones are
deleted when a new run starts.

## Layout

```
docker/            Dockerfile + entrypoint (Xvfb, XFCE, x11vnc, noVNC, at-spi, tesseract)
agent/a11y.py      AT-SPI walk + widget invocation (the fast perception path)
agent/vision.py    capture, OCR fallback, pixel-diff thumbs, annotated frames
agent/config.py    every question + threshold, single file for review
agent/ts.py        TypeSafe HTTP client (retries on 429/529)
agent/actions.py   xdotool wrappers (mouse/keyboard fallback path)
agent/loop.py      observe → ask → act loop, suite runner, verdicts
agent/server.py    FastAPI control panel
```

## Prior art

[awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use) does the same
OCR + classifier loop on macOS, much further along. Still worth stealing:

- changed-tile OCR cache (only re-read screen regions that changed)
- a small writer model for free text, since Jev cannot generate it
- run folders with numbered screenshots + the exact payload per step, for offline replay

## Known limits

- Typing only works for text already present in the task (put it in `"quotes"`) — Jev cannot
  generate text. Pair a small writer model for arbitrary prose.
- `done` is a single noul; nothing verifies the *state* of the app beyond the screen.
- Icon-only surfaces fall back to a 4x3 grid click.
- AT-SPI needs apps started after the a11y bus, and GTK/web coverage varies by toolkit.
