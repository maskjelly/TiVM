# Changelog

Versions are cut from merged pull requests and tagged on `main`. Earlier releases pre-date this
file.

## Unreleased — launch readiness

- Local Docker ports now bind to loopback by default; per-task automation has a finite 40-step
  default budget.
- Added a container-based CI workflow for the existing Python unit suite, a security policy, and a
  product readiness plan. The workflow has not yet completed its first hosted run.
- Rewrote the project overview to explain the product, surface the demonstrated replay result, and
  state the pre-1.0 and trusted-code limits clearly.

## 0.6.0 — replay: repeat a flow without calling the model

- **Trace recording.** When a flow passes while an app is under test, the runner stores the action
  trace (action, target role+text, typed text, key, point) plus up to six assertions — texts that
  appeared only after the flow ran. Traces live in `TIVM_TRACES_DIR` (host `./traces`) keyed by
  task + app URL, so they survive run pruning.
- **Replay first.** On a repeat of the same task against the same app URL, TiVM replays the trace
  with no planner calls: it re-finds each target by role+text (then normalised text, then recorded
  point within 24 px), performs the action, settles, and finally checks that the recorded
  assertions are on screen. Success returns `mode: replay`; the report shows a `replay` badge.
- **Divergence falls back.** A missing target or a failed assertion logs the reason, emits a
  `replay` timeline event, and explores the task with the planner as before (then records a fresh
  trace on success). `TIVM_REPLAY=0` disables the whole path.
- **Determinism work that replay forced:** Firefox autoconfig prefs (no first-run/onboarding),
  graceful `firefox --quit` between runs (a crash-restore infobar shifts the page), a single
  browser window per run, window activation before replay, and assertions scoped to the browser
  window with a chrome denylist. Verified on rove: suite re-run replayed both todo flows with
  **0 planner tokens** (34 s + 64 s vs 216 s + 41 s exploring).
- **Tests** — 11 new stdlib cases (46 total): normalisation/assertions, target matching, trace
  round trip, successful replay, divergence on missing target and on wrong final state.
- First slice of P3 in `docs/HOSTED.md`; budget/proxy work remains.

## 0.5.1 — rove desktop fix: vendored seccomp profile, kernel 5.15

- **Black desktop root cause found.** The host's libseccomp 2.5.1 does not know `close_range`
  (libseccomp 2.5.2+), so Docker drops that allow rule from any allow-list profile and the default
  errno applies. With EPERM, GLib's `g_spawn` fails and `xfce4-session` cannot start xfwm4, the
  panel, the desktop or Thunar. `deploy/seccomp-tivm.json` vendors Docker's default profile minus
  `close_range` with ENOSYS as the default errno (GLib falls back to `/proc/self/fd`); the desktop
  container uses it via `TIVM_SECCOMP_PROFILE`. Stock seccomp can return after a host upgrade to
  22.04+.
- **Kernel 5.15 on rove.** `linux-generic-hwe-20.04` is installed and booted; Bun 1.4 now serves
  normally (on 5.4 its event loop spun at 100% CPU and never bound a socket).
- Verified: XFCE processes up under the profile, orchestrator job on PR #2 ran both contract flows
  to pass.

## 0.5.0 — orchestrator: webhooks, queue, reports over one URL

- **`orchestrator/`** — a FastAPI service that turns events into runs:
  - `POST /webhook` with HMAC-SHA256 verification; `issue_comment` mentions (`@tivm`) and
    `pull_request` labels (`tivm`) become jobs; every event is stored with the job.
  - SQLite queue with claim/finish/supersede; a new push on the same PR cancels the previous job
    (the box is stopped only if that job was actually running).
  - Worker builds the dev-box run (`{repo, pr}` → the box's prepare phase), waits, records the
    verdict, and publishes.
  - Sticky PR comment (upserted by marker, never re-posted), check run with the verdict, failure
    screenshots inlined, per-flow video links.
  - Tokenized report serving: `/reports/<job>/<token>/report.html` plus videos and screenshots;
    wrong token is a 404 and nothing is listable.
  - `GET /` internal dashboard; `GET/POST /api/jobs`, `POST /api/jobs/{id}/cancel` (token-guarded).
- **Deploy** — orchestrator joins the hosted compose stack (`tivm-orchestrator`, 127.0.0.1:6090),
  `make tunnel` now forwards 6090 as well.
- **Tests** — 16 more stdlib cases (35 total): triggers, HMAC, store lifecycle and supersede,
  comment rendering, worker success/failure/timeout/supersede paths.
- Version 0.5.0.

## 0.4.0 — app contract and prepare phase

- **`.tivm.yml` contract.** A repo declares how to prepare and run its app (`app.dir`, `app.setup`,
  `app.run`, `app.url`, optional `app.ready`, `app.ready_timeout`, non-secret `app.env`) and its
  customer-facing `flows`. When a run gets no explicit tasks, the contract's flows are the suite.
- **Prepare phase.** `POST /api/run` accepts `app: {repo, ref | pr}`: TiVM clones the ref (PR refs
  included), reads the contract, runs setup, starts the app detached, waits for readiness, opens it
  in Firefox, and tells the planner that the app is already running so it never re-clones or
  restarts it. `POST /api/prepare` runs just the prepare half for debugging. Failures become a
  structured `prepare` verdict per task instead of a silent hang.
- **Inference fallback.** Without a contract, `package.json` is enough: lockfile or
  `packageManager` picks the installer, `dev`/`start`/`preview`/`serve` picks the script, and
  framework ports (vite, next, astro, ...) or an explicit `--port` pick the URL.
- **Demo app + dogfooding.** `examples/todo-app` (zero-dependency Node app) with the repo's own
  `.tivm.yml`: TiVM PRs can be tested by TiVM against its own demo.
- **Pixel clicks are mapped correctly.** The planner sees a screenshot downscaled to 1024 px wide;
  its `x/y` answers are now scaled back to real screen pixels instead of being used raw (a click
  meant for the todo input used to land on the tab strip, ~20% off).
- **Determinism.** Firefox enterprise policies suppress first-run onboarding and the
  default-browser prompt; `tests/` (15 stdlib unittest cases) runs in the container via `make test`
  or `make rove-test`.
- Runs on rove now force-recreate the container on deploy, so a deploy always serves the code and
  version that were just shipped.

## 0.3.0 — hosted foundation

- **Per-task video.** Runs are recorded with `ffmpeg` x11grab (`screen.mp4`) and cut into
  `task-N.mp4`; failures also get `task-N-failure.mp4` (the last 25 seconds before the verdict).
  `TIVM_VIDEO=0` turns recording off, ffmpeg-less images degrade gracefully.
- **Static run reports.** Every suite writes `report.html`: verdict header, one card per flow with
  video, inline failure details and final screenshot. Served at `/api/runs/{id}/report` and from
  the artifact mount; regenerated on demand for older runs.
- **Hosted runner on rove.** `deploy/provision-rove.sh` (Docker, 4 GB swap, ufw SSH-only),
  `deploy/deploy.sh`, a rove compose override, `make rove-*` targets and `make tunnel`. Hosted
  desktops publish 6080/6081 on `127.0.0.1` only (`TIVM_BIND`).
- **Plan for the hosted product.** `docs/HOSTED.md`: GitHub App + Cloudflare Tunnel triggers,
  orchestrator and job model, `.tivm.yml` app contract, record/replay cost strategy, trust model
  for untrusted PR code, and the P0–P5 roadmap (P0 verified on rove).
- Failure objects now carry video metadata (`video.file`, `video.failure_file`) and timeline events
  carry a monotonic `t` plus `task_index` for per-task filtering.
