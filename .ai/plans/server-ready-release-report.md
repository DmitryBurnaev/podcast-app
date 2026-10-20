# Issue #16 implementation and verification

Date: 2026-10-20.

Issue: https://github.com/DmitryBurnaev/podcast-app/issues/16

Branch: `feature/16-ci-builds-and-tests`, synchronized with `origin/main` before
implementation. Scope includes the expanded server-deployment plan agreed in
this conversation, beyond the original issue's CI-only description.

## Implemented

- A reusable checks workflow builds service/test images for explicit platforms,
  verifies loaded image architecture and media tools, runs isolated PostgreSQL
  functional tests, and smoke-tests migrations plus web/worker readiness.
  PRs use AMD64; `main` and release tags use AMD64 and ARM64.
- PR checks include frontend tests, deployment-script regression tests, and
  Black/Ruff/mypy. No production secrets or developer `.env` are used by tests.
- Release publication creates versioned and `latest` multi-platform GHCR images,
  verifies both platforms, and packages a deployment kit and metadata before
  GitHub Release creation. Automatic SSH deployment uses the same kit and is
  enabled explicitly with `DEPLOY_ENABLED=true` and the documented secrets.
- Docker images include ffmpeg/ffprobe, timezone/certificate data, and a writable
  media cache. Entrypoints propagate failures and signals; test mode does not
  migrate the application database.
- Local Compose retains the existing `.data` paths, supports configurable image
  references/platforms and a separate CI env file, and no longer uses globally
  fixed container names. Production Compose runs only web/background on the
  external `storage` network, validates required configuration, and selects one
  version for both roles.
- Production scripts resolve their own installation directory, pass dotenv
  files to Compose without executing them, pull before restarting, wait for
  readiness, and retain secrets/data and unrelated host images.
- The systemd unit defines installation paths, startup timeout, reboot startup,
  and Docker lifecycle dependencies. The Nginx example proxies all app routes.
- `DEPLOYMENT.md` documents server accounts/permissions, external network
  aliases, PostgreSQL/Redis/S3 setup, installation, updates, TLS, logs, and health.
- Existing Scan/CodeQL workflow artifact paths use valid `github.workspace`
  expressions; CodeQL's output directory is explicit.
- P9 in the testing migration plan is split into implemented CI preparation and
  still-pending full test typing/90% coverage. The coverage target is not waived.

## Verification performed

All commands were run separately; Docker resources were isolated under the
`podcast-issue16` Compose project with data in
`/private/tmp/podcast-issue16-data`, using only `.github/ci.env`.

| Check | Result |
| --- | --- |
| Production `service` image build, AMD64 and ARM64 | Passed on both platforms |
| Development `tests` image build, AMD64 and ARM64 | Passed on both platforms |
| Full container backend suite, final ARM64 image | 484 passed, 86% coverage |
| Full container backend suite, final AMD64 image | 484 passed, 86% coverage |
| Frontend JavaScript suite | 29 passed |
| Deployment scripts, packaging, Compose configuration, manifest gate | 13 passed |
| Health probes, including idle/busy workers, stale/foreign workers and DB failure | 13 passed |
| Black, Ruff, mypy in the AMD64 test image | Passed |
| Local Ruff and mypy, including explicit typing of the new test module | Passed |
| Bash/POSIX shell syntax | Passed |
| actionlint 1.7.12, all workflows | Passed |
| Development/CI Compose rendering | Passed |
| Production Compose rendering, version precedence, literal dollar values and external network | Passed |
| Actual service startup/migrations and web/worker health, both architectures | Passed |
| Synthetic MP3 encoding and ffprobe, both architectures | Passed, duration 0.100000s |
| HTTP health endpoint | Returned status=ok |
| Production Nginx example, actual nginx -t in an isolated image | Passed |
| git diff --check | Passed |

The script tests cover startup timeout diagnostics, failed image pulls without
restarting the running service, failed post-restart readiness, working-directory
independence, preserving dotenv contents, missing configuration, deployment-kit
contents, version precedence, and rejecting incomplete platform manifests.

Runtime smoke checks use dummy S3 configuration without making S3 calls. They
prove container startup, dependency connectivity and codecs, not a successful
external download or upload.

The temporary Compose containers and network were removed after verification.
The built images and disposable data directory remain available locally; no
host-wide cleanup was performed.

## Remaining acceptance

- Hosted GitHub Actions have not run for these unpushed changes. Actual GHCR
  publication, GitHub Release assets, registry permissions and SSH deployment
  need a subsequent authorized PR/tag run.
- No Linux server/systemd session or production credentials were provided.
  First installation, reboot recovery, unit activation, real TLS/Nginx routing,
  production S3/media/RSS flows, and an upgrade still need the documented server
  walkthrough. CI includes `systemd-analyze verify`; local macOS has no systemd.
- Ordinary tests still use external-boundary fakes as required by the project's
  testing strategy; they do not replace the server acceptance checklist.
- Changes are left for review without a commit, push, or GitHub issue mutation.
