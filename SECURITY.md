# Security policy

## Supported versions

Security fixes target the latest version on the `main` branch. TiVM is pre-1.0; no stable release
or long-term support promise has been made.

## Reporting a vulnerability

Please do not publish exploit details in a public issue. Use GitHub's private vulnerability
reporting for this repository when available. If it is unavailable, contact the maintainer through
the GitHub profile and include reproduction steps, impact, and any proposed mitigation. We will
acknowledge reports as soon as practical and coordinate a fix before public disclosure.

## Safe-use boundary

TiVM runs shell commands and installs dependencies inside its desktop container. Treat tasks,
repositories, package scripts, and model output as untrusted code. The container is a useful local
disposable workspace, not a hardened security boundary. Do not run untrusted repositories with
valuable credentials mounted or environment variables present. The hosted pull-request runner is
for trusted repositories only until credential and per-job execution isolation are implemented;
see [the readiness plan](docs/READINESS.md).
