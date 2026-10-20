# Prepare server-ready releases with multi-platform CI/CD and systemd deployment

## Summary

Provide a working path from a release tag to a running Podcast App on a Linux
server, following the CI/CD and deployment structure in `code-agent`.

## Requirements

- Enable PR linting, backend tests, and frontend tests; build and test AMD64 and
  ARM64 on `main`.
- Publish versioned and `latest` GHCR images for `linux/amd64` and
  `linux/arm64`, including media-processing dependencies. Gate release and
  deployment on successful tests and verification of both platforms.
- Align development, test, and production Compose configurations. Production
  runs web and background workers from the same release, connecting to external
  PostgreSQL and Redis containers through the documented Docker network
  `storage`.
- Complete the production `etc` kit: Compose, environment template, Nginx
  example, systemd unit, and deployment/management scripts.
- Support installation under `/opt/podcast-app`, startup on boot, migrations,
  readiness checks, updates, and service start/stop/restart/status/logs/health.
- Document initial server setup, network aliases, external S3, secrets, image
  selection, and the manual and CI deployment procedures.

## Acceptance Criteria

- Both architectures build and pass tests; published manifests contain both
  platforms.
- The documented procedure installs a released version without server-side
  builds or undocumented edits.
- Web, workers, external PostgreSQL/Redis, and S3 work together; a smoke test
  covers login, episode processing, and RSS/media access.
- Systemd startup, reboot recovery, management commands, and release updates
  work; updates preserve configuration and data, and failed deployments report
  actionable errors.

## Implementation Notes

The full implementation plan is in
[`.ai/plans/server-ready-release-plan.md`](server-ready-release-plan.md).
Extend the existing setup using `../code-agent` as a reference. External
infrastructure provisioning and the separate coverage migration are outside
this task.
