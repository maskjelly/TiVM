# TiVM

Computer-use test harness: a throwaway Linux desktop in Docker, driven by
[TypeSafe](https://docs.typesafe.ai)'s **Jev** classifier instead of a frontier model.
Type a task (or a suite of tasks) in the web panel; the loop perceives the screen, asks Jev
typed questions, acts, and returns a pass/fail verdict per task — the shape a PR bot would post.

## Perception: Jev is text-only, so the screen must become text

| source | cost | what it gives |
| ------ | ---- | ------------- |
| **AT-SPI accessibility tree** (primary) | ~50ms | role + name + coordinates for real widgets; clicks can be *invoked on the widget* instead of moving the mouse |
| **tesseract OCR**, 2x upscale (fallback) | ~400-700ms | text on surfaces a11y can't see (canvas, web content, non-GTK apps); runs only when a11y coverage is thin |

Submenus are exposed as role `menu` (marked "opens a submenu") vs `menu item`, so Jev can
navigate menus it has never seen. A 160x100 pixel-diff thumb drives change detection and the
"did my action do anything" check (a hash is useless — the panel clock ticks every minute).

## One step

```
perceive (a11y + optional OCR) → one planner call → act → settle
```

### Planners (`TIVM_PLANNER`)

| mode | model | sees pixels | generates text | measured per step |
| ---- | ----- | ----------- | -------------- | ----------------- |
| `openai` (default) | `TIVM_OPENAI_PLANNER_MODEL`, default `gpt-5.6-sol` with `reasoning=none` | yes — screenshot in the same call | yes (any shell command, search query, message) | ~2.0–2.7s, ~2.6k tokens |
| `jev` | TypeSafe `jev-latest` | no — text state only | no (types only text found in the task) | ~0.9s decide, ~1.5k in / 0.25k out |

In `openai` mode the a11y tree is the only perception source (the planner reads the screenshot
itself), so tesseract is skipped entirely — that is the atomic single-call path.
Perception modes: `TIVM_PERCEPTION=vision` adds OpenAI OCR text items (`gpt-5.6-sol`),
`a11y` uses none, `hybrid` keeps tesseract as the Jev-mode fallback.

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
make vm          # one-time: colima VM, 2 vCPU / 4 GB RAM / 40 GB disk
make up          # always starts fresh: down + prune dangling layers + recreate
make runs        # list exported run folders
make wipe-runs   # delete all artifacts
make clean       # down + remove dangling images and build cache (frees GBs)
```

`TIVM_KEEP_RUNS` (default 20) caps how many run folders are kept; older ones are
deleted when a new run starts.

## Run it

Requires a container runtime (Docker CLI + a VM, e.g. Colima on macOS):

```sh
colima start --memory 4 --cpu 2
make up          # builds the image and starts the desktop (~3-6 min first time)
```

- **Control panel** — http://localhost:6081 (type tasks, watch decisions, see live screen, verdict JSON at `/api/result`)
- **Desktop (noVNC)** — http://localhost:6080/vnc.html?autoconnect=1&resize=scale

The TypeSafe org behind `TYPESAFE_API_KEY` needs credits — without them every step fails with
HTTP 402 (add them at console.typesafe.ai/settings/billing).

Try a preset, or:

```
Open the text editor (Mousepad) and type "hello from Jev"
Open the file manager and open the "Documents" folder
Open the Web browser and search the web for "typesafe ai"
```

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
