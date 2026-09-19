# TiVM Hosted — PR-triggered computer-use testing

Status: P0 done on rove. This document is the reference for turning TiVM from a local control
panel into a hosted service where a PR author only ever sees a check, a comment and a report.

## Goal

A developer opens or pushes a PR and comments `@tivm test` (or the repo labels it). A machine on
`rove` pulls the PR, starts the app the way a customer would meet it, walks the tabs, links and
flows with the computer-use agent, and posts back:

- a verdict per flow (pass/fail, steps, duration),
- a **video** of each flow and a shorter clip of what happened right before a failure,
- the failure story in prose plus the structured failure object,
- a static report page with everything, linked from a sticky PR comment and a GitHub check.

The TiVM control panel and live desktop are **internal tooling**. PR authors never see them.

## Topology

```
GitHub ──webhook (HMAC)──► rove: tivm-orchestrator
   ▲                             │  job queue (SQLite), concurrency 2
   │                             ▼
   │                       dev box N (docker: Xvfb + XFCE + Firefox + agent)
   │                             │  artifacts: run.json, timeline.json, task-N.mp4, shots
   │                             ▼
   └── check + sticky comment ◄── report.html ── public URL via Cloudflare Tunnel
                                   (report + video only; panel and noVNC stay tunnel-only)

Mac = client: ssh tunnel for internal dashboard, live debugging, replays. No hosted workloads.
```

Rove (measured): Ubuntu 20.04, 4 vCPU, 7.6 GB RAM, 98 GB disk. No swap and no Docker initially.
Two concurrent dev boxes is the safe ceiling; a third only if the app under test is light.

## Decisions

| question | decision | why |
| -------- | -------- | --- |
| Trigger | GitHub App + Cloudflare Tunnel | scoped installation tokens, and only our orchestrator executes framework code; PR-provided workflow files never run |
| Video/report host | served by the orchestrator on rove through the tunnel | zero extra infra, Cloudflare terminates TLS; R2 only if durability/CDN is needed later |
| Where workloads run | rove | the Mac stays the client; hosted runs must not touch it |
| How "tests" are written | repo contract `.tivm.yml`, PR comment override, inference fallback | deterministic, reviewable, no guessing from task text |
| Cost strategy | record once, replay many | the LLM explores a flow one time; later PRs replay its trace with zero planner calls and fall back to the planner only on divergence |
| Trust boundary | planner keys never enter the dev box; egress allowlist; resource caps | PR code is untrusted, especially forks |

## Components

| piece | lives in | responsibility |
| ----- | -------- | -------------- |
| orchestrator | `orchestrator/` (new) | webhook intake, job queue, docker worker lifecycle, GitHub App client, budget guard, report publishing, pruning |
| agent | `agent/` (existing) | perceive → decide → act → settle; per-task video; `prepare` phase; replay executor; report generator |
| deploy | `deploy/` (new) | rove provisioning script, hosted compose profile, deploy script, tunnel |
| cli | `tivm` entrypoint (new) | `tivm request <pr>`, `tivm replay`, `tivm report` for humans |

## Contracts

`.tivm.yml` in the app repo (all keys optional; inference fills gaps):

```yaml
tivm: 1
app:
  setup: ["bun install"]           # cached between runs
  run: ["bun run dev"]
  url: "http://localhost:3000"
  ready: "curl -sf localhost:3000"
flows:
  - name: signup
    steps: "as a new customer, sign up with a fresh email"
    expect: "the dashboard is visible"
  - name: checkout
    steps: "add the first product to cart and reach payment"
```

Job spec (orchestrator → worker):

```json
{
  "repo": "owner/name", "pr": 42, "sha": "…", "base": "…", "actor": "…",
  "trigger": "comment|label|push", "flows": "auto|smoke|[names]",
  "budget": {"llm_usd": 0.25, "minutes": 20}
}
```

Job lifecycle: `queued → preparing → testing → reporting → done|failed|cancelled`. A new push on
the same PR cancels the superseded job (key `repo#pr`).

## Reporting

Per run, generated from `run.json` + `timeline.json` + video, no live UI:

- `report.html` — verdict header, one card per flow: badge, reason, video element, inline failure,
  final screenshot, collapsible step timeline.
- `task-N.mp4` — the full flow. `task-N-failure.mp4` — the last ~25 s before the verdict.
- Failure prose rendered from the existing structured object (`stage`, `step`, `action`, `typed`,
  `check`, `screen`) so the reader knows where and how it broke without watching the video.
- GitHub: one sticky comment (edited on every push, never re-posted) with the verdict table,
  failure screenshots inline, and links to the report and each video; one check run whose
  conclusion mirrors the suite.

## Cheap and fast

1. **Replay** — a passing flow's action trace (a11y targets, keys, text, assertions) is frozen and
   replayed without planner calls. Divergence (target missing, unexpected screen) falls back to
   the planner for that flow only and re-freezes on success.
2. **A11y-first perception** — free and ~50 ms; vision is a fallback for canvases.
3. **Warm layers** — a per-repo dependency cache volume and a cached app image layer so a PR does
   not pay cold install costs.
4. **Delta selection** — `git diff base..head` maps changed paths to flows; affected flows plus the
   smoke set run first, the rest only with remaining budget.
5. **Budgets** — per-run minutes and LLM dollars enforced by the orchestrator, plus a per-repo
   daily cap. Skip reasons are reported, never silent.
6. **LLM proxy** — the dev box calls the orchestrator with a per-run token; the real key never
   enters the box, and the proxy is where caching and accounting live.

Targets to calibrate against: first screenshot < 90 s after a push (warm cache), replay flow
< 60 s at ~0 planner tokens, full report < 10 min, LLM cost per PR < $0.10 in the steady state.

## Trust model

The dev box runs untrusted PR code (fork PRs especially). Rules:

- Planner keys, the GitHub App key and installation tokens never enter the box; LLM calls go
  through the proxy.
- No `docker.sock`, no host network, no SSH agent, no cloud metadata (`169.254.169.254` in
  particular), read-only agent mount, per-job `runs/` directory.
- `--cpus`, `--memory`, `--pids-limit` and wall-clock timeouts per job; a global disk watchdog.
- Egress allowlist: package registries, GitHub, the proxy. Everything else denied.
- Same-repo PRs are treated as trusted for now; fork PRs are labelled best-effort and move to
  per-run VM isolation (Kata/gVisor/Firecracker) in P5, with the agent outside the untrusted
  boundary reading pixels over VNC.
- Trigger gating: only users with write/triage may start runs; per-user rate limits; kill switch.

## Roadmap

| phase | deliverable | acceptance |
| ----- | ----------- | ---------- |
| **P0 done** | rove provisioned; hosted compose profile; remote deploy and tunnel targets; per-task video; static report | a suite on rove yields `task-N.mp4` + `report.html`, watched from the Mac over the tunnel — **verified 2026-09-19**: 1/2 flows passed, both clips reviewed, report served over `make tunnel` |
| **P1 done** | `.tivm.yml` contract, prepare phase (clone at ref/PR, setup, launch, ready check, Firefox), inference fallback, `/api/prepare`, contract flows as the suite | the P1 PR itself is tested by rove against `examples/todo-app` on the PR branch (todo flows pass/fail correctly, report carries the app info) |
| P2 | GitHub App, queue, concurrency 2, cancel-on-push, sticky comment + check, report URL | `@tivm test` on a real PR produces a report link in under 10 minutes; the panel stays private |
| P3 | replay executor, divergence fallback, LLM proxy, dep caches, budgets | replay flow < 60 s at ~0 planner tokens; a seeded UI regression is still caught |
| P4 | nav/link enumeration → generated smoke flows; diff→flows mapping | all top-level surfaces of a reference app are covered within budget, skips reported |
| P5 | egress allowlist, caps, quotas, kill switch, fork isolation | a hostile PR cannot reach keys, other runs or the host; the box survives starvation |

## Gaps this design closes (current code)

| today | change |
| ----- | ------ |
| single run per container, `409` when busy (`agent/server.py:42`) | orchestrator owns box lifecycle and queuing |
| run IDs are timestamps (`agent/loop.py:173`) | orchestrator assigns job-scoped run IDs |
| planner keys ship into the box via `env_file` | LLM proxy with per-run tokens |
| still images only (`agent/vision.py`) | per-task `ffmpeg` x11grab video + failure clips |
| panel is the only UI (`agent/static/index.html`) | static `report.html` for PR authors; panel stays tunnel-only |
| prep guessed from task text (`agent/loop.py:26`) | explicit `prepare` phase driven by `.tivm.yml` |
| ports published on `0.0.0.0` (`docker-compose.yml`) | hosted profile binds `127.0.0.1` and is reached only through the tunnel |
| colima-only `Makefile` | rove provision/deploy/tunnel targets + `deploy/` scripts |

## Operations

- Provision: `deploy/provision-rove.sh` (Docker, 4 GB swap, ufw with SSH only, unattended
  security upgrades), run over SSH as `make rove-setup`.
- Deploy: `make rove-deploy` rsyncs the tree to `/opt/tivm` and restarts the hosted compose
  profile (`docker-compose.yml` + `deploy/docker-compose.rove.yml`).
- See things: `make tunnel` forwards the panel (6081) and live desktop (6080) to the Mac.
- Artifacts: `runs/` on rove, pruned by `TIVM_KEEP_RUNS`; report and videos are served from there.
- `TIVM_BIND=127.0.0.1` keeps 6080/6081 off the public interface; ufw is the second layer.

### rove notes (from P0)

- **Kernel 5.4 vs seccomp.** Ubuntu 24.04's glibc/GLib spawn children with `close_range(2)`
  (kernel 5.9+). Docker's default seccomp profile answers the unknown syscall with EPERM, so
  GLib's `g_spawn` fails and `xfce4-session` cannot start xfwm4, the panel, the desktop or
  Thunar — the screen stays black. Plain fork/exec is unaffected. `deploy/docker-compose.rove.yml`
  drops seccomp for the desktop container so runs work today. Proper fix, needs one reboot:
  `apt-get install -y linux-generic-hwe-20.04 && reboot`, then delete that override.
- The Docker convenience script refuses focal (packages it wants do not exist there); the
  provision script installs `docker-ce`, `docker-ce-cli`, `containerd.io`,
  `docker-compose-plugin` and `docker-buildx-plugin` from Docker's own apt repo instead.
- Capacity: 4 vCPU / 7.6 GB RAM / 88 GB free, 4 GB swap. Two concurrent dev boxes is the safe
  ceiling; a run of two flows took ~65 s wall clock and ~2 MB of video.
- Ubuntu 20.04 is EOL for Docker tooling; upgrade to 22.04/24.04 when convenient (do the kernel
  upgrade first, it also removes the seccomp workaround).
