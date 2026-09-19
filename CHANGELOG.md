# Changelog

Versions are cut from merged pull requests and tagged on `main`. Earlier releases pre-date this
file.

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
- **Demo app + dogfooding.** `examples/todo-app` (zero-dependency Bun app) with the repo's own
  `.tivm.yml`: TiVM PRs can be tested by TiVM against its own demo.
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
