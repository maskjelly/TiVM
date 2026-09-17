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
perceive (a11y + optional OCR) → one TypeSafe request → act → settle
```

Questions per step (all in one request): `done` (noul), `action` (choice), `target` (choice over
detected widgets). Follow-up questions (`key`, `grid`) are only sent when the action needs them.

Measured on the demo task (open the text editor, type `"hello from Jev"`):

- step: **~1.4–1.7s** — Jev ~0.85–1.0s, perceive 0.06–0.35s, act 0–0.2s, settle 0.2–0.4s
- tokens: ~1.3–1.8k in / ~0.2–0.4k out per step
- task: 6 steps, 10.3s wall, 10.4k in / 1.7k out total

## What's in the sandbox

A throwaway Ubuntu 24.04 dev box: `apt` (with package lists kept, so `apt-get install` works
in-session), `git`, Python 3 + venv + pip, Node 24 + npm, Bun, Firefox (Mozilla build, a11y
visible, first-run dialogs pre-seeded away), Epiphany, `gcc`/`make`/`pkg-config`/`libssl-dev`,
`curl`/`wget`/`git`/`jq`/`unzip`/`sudo`, XFCE desktop, tesseract, xdotool, at-spi. `make check`
prints every version.

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
