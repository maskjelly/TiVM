# TiVM readiness plan

## Current state

TiVM is a functional prototype with disposable desktop runs, app preparation, flow contracts,
replay, artifacts, and a hosted-runner prototype. It is suitable for local experiments and a
trusted-repository pilot. It is not ready to execute untrusted public pull requests.

## Before a trusted pilot

- Run the example app and at least three unrelated apps repeatedly; record pass rate, false pass
  rate, replay rate, runtime, and model cost.
- Publish a CI workflow that builds the image and runs the unit/integration suite on every change.
- Make a clean-machine install and recovery guide, then have someone other than the maintainer
  follow it.
- Keep binding loopback by default and use a finite per-task action budget. These local defaults
  are now in place.

## Before public pull requests

1. **Credential isolation:** provider keys must never enter the desktop/job container. Put model
   calls behind a proxy that authorizes short-lived, per-job tokens and enforces usage budgets.
2. **Execution isolation:** run each PR in a disposable VM or equivalent boundary, as a non-root
   user, with no host mounts or Docker socket. Restrict outbound network access and bound CPU,
   memory, disk, process count, wall time, and artifact size.
3. **Service hardening:** require authentication for control endpoints, validate webhook and job
   payloads, rate-limit submissions, bound queue growth, and ensure report URLs do not expose
   secrets or cross-job files.
4. **GitHub integration:** register a least-privilege GitHub App; verify permissions, cancellation
   on new commits, check conclusions, and artifact retention on a real test repository.
5. **Evidence:** exercise malicious setup scripts, network exfiltration attempts, timeouts, worker
   crashes, disk exhaustion, duplicate webhooks, and concurrent jobs before enabling public repos.

The hosted topology and environment-isolation blocker are tracked in [HOSTED.md](HOSTED.md).
Until gates 1 and 2 are complete, accept only repositories whose code and dependencies are trusted.
