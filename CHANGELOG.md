# Changelog

Versions are cut from merged pull requests and tagged on `main`. Earlier releases pre-date this
file.

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
