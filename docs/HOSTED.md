# Hosted runner

TiVM has a single-worker hosted runner for repositories that the operator trusts. It accepts a GitHub webhook or an authenticated manual job, runs the app in the desktop container, and stores a report. It can post a PR comment and check when a GitHub token is configured.

**Trust limit:** the desktop container receives `.env`, including model provider keys. App setup scripts and model chosen commands run in that container. Use trusted repositories and dependencies only. Fork PRs are rejected. Do not offer this service for public, untrusted PRs. See [Readiness](READINESS.md).

## What is implemented

- An orchestrator in `orchestrator/` verifies webhook signatures and the trigger actor's GitHub permission. It accepts only repositories in `TIVM_ALLOWED_REPOS`.
- `@tivm` on a PR comment or the `tivm` PR label can trigger a run. Set `TIVM_RUN_ON_OPEN=1` to also run opened or updated PRs that carry the label.
- PR jobs resolve the current same-repository head commit. The desktop checks that commit before it runs app setup. Fork PRs and changed heads are rejected.
- SQLite stores jobs. Duplicate webhook deliveries use the existing job. New work supersedes an older job for the same PR. One worker owns one desktop container.
- The runner saves verdicts, screenshots, step history, and video when enabled. The orchestrator serves reports at tokenized URLs and can post a PR comment and check.
- Job APIs and the dashboard require `X-TiVM-Token`. The desktop's mutating APIs require a bearer token when `TIVM_TOKEN` is set. Both service ports bind to loopback by default.

## Configure

The hosted Compose override is [`deploy/docker-compose.rove.yml`](../deploy/docker-compose.rove.yml). It starts the desktop and orchestrator together. Set these values in `.env` before deploying:

| Variable | Required use |
| --- | --- |
| `OPENAI_API_KEY` or `TYPESAFE_API_KEY` | Planner; select TypeSafe with `TIVM_PLANNER=jev`. |
| `TIVM_API_TOKEN` | Authenticates job APIs and the dashboard. Hosted startup requires it. |
| `TIVM_ALLOWED_REPOS` | Comma-separated `owner/repo` allowlist. Hosted startup requires it. |
| `TIVM_TOKEN` | Authenticates desktop control requests; the orchestrator forwards it. |
| `TIVM_WEBHOOK_SECRET` | Verifies GitHub webhooks. Required for webhook intake. |
| `TIVM_GITHUB_TOKEN` | Checks trigger permissions and posts PR results. Required for webhook intake. |
| `TIVM_PUBLIC_URL` | Base URL used in report links posted to GitHub. |

Keep `TIVM_CONCURRENCY=1`; one worker owns one desktop. Keep ports 6080, 6081, and 6090 private. A public report URL needs an operator-managed route to the orchestrator. The repository does not configure Cloudflare Tunnel or a GitHub App.

The included host script targets an SSH host named `rove` by default. Review it and the target host before use:

```sh
make rove-deploy             # syncs code and .env, then restarts Compose
make rove-status             # checks containers and disk use
make tunnel                  # forwards 6080, 6081, and 6090 over SSH
```

`make rove-setup` provisions the host and changes its packages and firewall rules. Use it only on a host you control. `deploy/deploy.sh` copies the local `.env` to the host.

## Trigger and inspect a job

For GitHub webhooks, configure `issue_comment` and `pull_request` events with the same secret as `TIVM_WEBHOOK_SECRET`. The actor needs write, maintain, or admin permission. The repository and PR head must meet the trust checks above.

For a manual job, tunnel port 6090 and use a trusted allowlisted repository:

```sh
curl -fsS -X POST http://127.0.0.1:6090/api/jobs \
  -H "X-TiVM-Token: $TIVM_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"repo":"owner/repo","pr":42}'

curl -fsS http://127.0.0.1:6090/api/jobs \
  -H "X-TiVM-Token: $TIVM_API_TOKEN"
```

`GET /healthz` is public on the orchestrator. `GET /api/jobs/{id}` and `POST /api/jobs/{id}/cancel` also require `X-TiVM-Token`. The dashboard at `/` requires that header; a normal browser request cannot supply it. Use the API or an authenticated internal proxy for dashboard access. Report URLs contain a per-job token; handle them as sensitive links.

## Recovery

- Intake holds at most 64 queued or running jobs. When full, it returns HTTP 429.
- On restart, queued jobs remain queued. Jobs that were running become failed with an unknown outcome. Inspect the run and external effects before retrying.
- Before upgrading, let jobs finish, stop the orchestrator, and back up `data/`, `runs/`, and `traces/`. Keep the prior image and config. After restart, check `/healthz`, list jobs with the API token, and run one trusted staging job. Restore the image and matching database backup if validation fails.

## Work still required for public PRs

Provider keys must move out of the desktop container. Each job needs a disposable boundary with restricted network access and resource limits. A least-privilege GitHub App, public routing, and repeatable reliability evidence also remain open. [Readiness](READINESS.md) tracks these gates.
