# Prepare server-ready releases with multi-platform CI/CD and systemd deployment

Status: implemented locally for GitHub issue #16; hosted release and server
acceptance remain pending. See `server-ready-release-report.md` for results.

## Working checklist

- [x] Read issue #16, confirm the expanded scope, and synchronize the existing
  `feature/16-ci-builds-and-tests` branch with `main`.
- [x] Align Docker images, entrypoint, health checks, and Compose configurations.
- [x] Complete the production scripts, systemd unit, and Nginx example.
- [x] Implement CI checks, multi-platform releases, and deployment packaging.
- [x] Document installation, updates, prerequisites, and verification commands.
- [x] Run automated checks and record results and remaining server validation.
- [ ] Run hosted Actions and publish/verify an actual GHCR release after review.
- [ ] Validate installation, reboot, upgrade, and real S3/media flows on a Linux
  server with systemd and TLS.

## Summary

Provide a complete, documented path from a GitHub release tag to a running
Podcast App installation on a Linux server, supporting both `linux/amd64` and
`linux/arm64`.

Use the neighboring `../code-agent` project as a reference for CI/CD, Docker
Compose, production configuration, and systemd management scripts. Adapt the
existing Podcast App setup to its web application, background workers, and
media-processing requirements.

## Requirements

### CI and release images

- Run linting, backend tests, and frontend JavaScript tests for pull requests
  targeting `main`.
- On pushes to `main`, build the service and test images and run backend tests
  on both AMD64 and ARM64.
- For release tags, require successful tests and publish multi-platform service
  images to GHCR under the version tag and `latest`.
- Verify that both published tags contain `linux/amd64` and `linux/arm64` before
  creating the GitHub Release or deploying.
- Include all runtime dependencies needed for media processing, including
  `ffmpeg`, in both image variants.
- Keep deployment files and scripts available with the corresponding release so
  installation does not require building images on the server.

### Docker Compose and configuration

- Make local development, CI testing, and production Compose configurations
  consistent in image selection, service roles, environment variables, and
  startup behavior.
- Production Compose must run `web` and `background` from the same released
  image and version.
- Connect both services to the existing external Docker network named
  `storage`. Document how to create it and attach the separately managed
  PostgreSQL and Redis containers.
- Configure database and Redis connections through `DB_HOST`, `REDIS_HOST`, and
  the existing connection settings. Document the required network aliases and
  internal ports.
- Keep production PostgreSQL and Redis outside the application's Compose
  lifecycle.
- Document external S3 configuration, required secrets, application URL, ports,
  and writable directories.
- Use `.env` for runtime configuration and `.version` for the selected
  `DOCKER_IMAGE` and `DOCKER_IMAGE_TAG`, consistently across scripts and Compose.

### Production deployment and systemd

- Complete the production deployment kit under `etc`: Compose configuration,
  environment template, Nginx example, systemd unit, and management scripts.
- Use `/opt/podcast-app` as the documented installation directory and
  `podcast-app.service` as the systemd unit.
- Document creation of the `podcast-app-srv` service account, directory
  permissions, Docker access, unit installation, and startup on boot.
- Make `start`, `stop`, and `deploy` resolve the installation directory reliably,
  regardless of the caller's working directory.
- Support `start`, `stop`, `restart`, `status`, `logs`, and `health` through the
  service helper.
- Apply database migrations before making the web application available; start
  background workers after web readiness.
- Make deployment pull the selected release, restart the application, and fail
  with useful diagnostics if readiness is not reached within a bounded timeout.
- Preserve server-managed secrets during updates and avoid host-wide image
  cleanup.
- Adapt the existing SSH deployment workflow to deliver the complete deployment
  kit and invoke the same deployment procedure used manually.
- Correct the Nginx example to serve the UI, static assets, API, and public
  media/RSS routes using the configured application port.

## Acceptance Criteria

- PR checks pass, and `main` builds and tests both architectures without silently
  substituting AMD64 for ARM64.
- Published release manifests contain both required platforms; an incomplete
  release cannot proceed to deployment.
- Following the installation guide on a prepared Linux server starts the
  released application without source builds or undocumented file edits.
- Web and background containers connect to the separately managed PostgreSQL
  and Redis containers over `storage`.
- Systemd starts the application after reboot, and all documented management
  commands work.
- Deployment reports application readiness rather than relying only on
  systemd's active state.
- A server smoke test verifies login, podcast and episode creation, background
  audio processing, S3 upload, and RSS/media access.
- Updating to another release preserves configuration and application data.
- Missing configuration, an unavailable dependency, or failed migrations produce
  an actionable failure.
- Image runtime checks cover both architectures; the complete installation and
  update procedure is verified on a disposable server.

## Implementation Notes

- Extend the existing workflows and deployment files rather than introducing a
  parallel release mechanism.
- Use `code-agent` as a structural reference; correct inherited assumptions and
  leftover names.
- CI functional tests must use an isolated PostgreSQL database configured
  through `TEST_DB_NAME`.
- Follow `../testing/knowledge-base.md` and update `../testing/plan.md` for any
  CI migration scope completed by this task.
- Document prerequisites: Docker Engine with Compose, systemd, the external
  `storage` network, PostgreSQL, Redis, S3, and host Nginx/TLS configuration.
- Provisioning external infrastructure and completing the separate coverage
  migration are outside this task.
