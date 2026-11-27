# Podcast App

A web application for creating podcasts, containing both an API and a user
interface, with background workers for media processing.

## Server installation

See [INSTALL.md](INSTALL.md) for release installation, external PostgreSQL/Redis
networking, S3 configuration, systemd, Nginx/HTTPS, and upgrades. Releases support
`linux/amd64` and `linux/arm64`.

## Development

### Prerequisites

- Python 3.14 and uv for the source checkout.
- Docker Engine/Desktop with Compose and Buildx for the local container stack.
- An existing S3-compatible bucket and its credentials.
- Node.js 22 or newer for the frontend tests.

The local Compose stack starts its own PostgreSQL and Redis. Production connects
to externally managed services instead.

### Local Docker quick start

From the repository root:

```sh
make install
cp .env.template .env
make secrets
```

Edit `.env` to configure your real S3 endpoint, region, bucket and credentials.
Keep the HTTP development URLs and `AUTH_COOKIE_SECURE=false` for local use.
Then start web, workers, PostgreSQL and Redis:

```sh
make run-in-docker
```

Open [http://localhost:8003](http://localhost:8003). Application data stays under
`.data`; the database password must match the role initialized in an existing
PostgreSQL data directory.

### Secret setup

`make secrets` fills empty or missing `APP_SECRET_KEY`, `SENS_DATA_ENCRYPT_KEY`,
`DB_PASSWORD` and `ADMIN_PASSWORD`, sets `.env` permissions to `600`, and prints
only the names it generated. Existing values survive repeated runs. For an
existing database, set its real password before running this command.

The command uses `src.cli.generate_secrets`. To target a different dotenv file:

```sh
uv run python -m src.cli.generate_secrets --env-file /path/to/development.env
```

### Run from source

Provide PostgreSQL and Redis reachable from your host and configure their
addresses in `.env`. Install `ffmpeg` and `ffprobe` on the host for media jobs
(the Docker image already includes them). Start web and worker in separate terminals:

```sh
make run
make run-worker
```

### Quality checks

```sh
make format
make lint
make test-in-docker
make test-frontend
```

Functional tests require the explicit, separate `TEST_DB_NAME`; use a dedicated
test database rather than the application's database. Test PostgreSQL roles
need permission to create the isolated database. `make test` runs the backend
suite against host-accessible dependencies.

## Local platform verification

For local checks, use `.github/ci.env` explicitly; no production `.env` is needed.
The database fixture creates a separate `TEST_DB_NAME` database and needs a
CI-only PostgreSQL role with `CREATEDB`. Infrastructure data defaults to `.data`;
set `PODCAST_DATA_DIR` to a disposable absolute directory for an isolated run.

```sh
APP_PLATFORM=linux/arm64 make build-platform
APP_PLATFORM=linux/arm64 make check-platform
```

Repeat the last two commands with `linux/amd64`. The platform check verifies the
actual loaded image architectures and media tools, starts PostgreSQL/Redis,
and runs the backend suite without rebuilding images. Tear down that test stack
with `docker compose --env-file .github/ci.env --profile test down`. Running images
for a different host architecture requires locally configured QEMU/binfmt support.
CI uses native runners for each architecture.

## CI/CD

- PRs targeting `main` and pushes to `main` (`tests.yaml`): service/test image
  builds and backend tests on native `ubuntu-24.04` (AMD64) and
  `ubuntu-24.04-arm` (ARM64) runners, plus Black/Ruff/mypy, frontend tests,
  and shell/production configuration validation.
- Version tags matching `1.2.3`: the same checks on both platforms, then native
  per-platform service builds published to GHCR by digest. A separate merge job
  combines both digests into one multi-platform image as the version and `latest`.
  Both manifests are checked before GitHub Release creation or deployment.
- Both platform jobs smoke-test migrations, web and worker readiness, and
  synthetic MP3 encoding/probing. S3 values in `.github/ci.env` are dummy values;
  this smoke test does not verify a real bucket or external media providers.
- Releases include the deployment kit and `release-metadata.json`.
- See [automatic deployment](INSTALL.md#automatic-deployment) for server
  configuration and SSH secrets. Ordinary CI checks receive no production secrets.

### Trivy scans

`scan.yaml` runs Trivy on PRs targeting `main`, pushes to `main`, and the
weekly schedule. The filesystem scan checks dependencies, including development
dependencies in `uv.lock`, and misconfigurations. The image scan checks OS and
language packages in the `linux/amd64` build of the `service` target.
The `trivy-fs` and `trivy-image` JSON artifacts are retained for 60 days. Trivy
prints a table from each uploaded report and fails the job on LOW, MEDIUM, HIGH,
or CRITICAL findings, including unfixed vulnerabilities.

CI verifies the pinned Trivy 0.76.2 binary against the release checksum before
extraction. [Official release](https://github.com/aquasecurity/trivy/releases/tag/v0.76.2)

The filesystem scan uses the binary's embedded checks. The runner still
fetches the vulnerability database, and the image build uses the base image
tags in `Dockerfile`, so scan results can change between runs. The upstream
advisory documents the earlier compromise of release and action tags.
[Trivy security advisory](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)

#### Image scan exception for pip's setuptools SBOM

Both inspected service images use Python 3.14.10. The bundled `ensurepip`
wheel (pip 26.2.2) and installed pip 26.3 list `setuptools` 70.3.0 in
`pip/_vendor/bom.cdx.json`. Inspection on AMD64 and ARM64 found only pip's
vendored `pkg_resources` from setuptools and confirmed that setuptools is
absent as an installed or importable package. [pip vendoring notes](https://github.com/pypa/pip/blob/26.3/src/pip/_vendor/README.rst)

The three advisories cover `PackageIndex`, source distribution manifest
handling on macOS, and legacy wheel installation on Windows.
[CVE-2025-47273](https://github.com/advisories/GHSA-5rjg-fvgr-3xxf),
[CVE-2026-59890](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c),
[GHSA-xmj3-9gf7-h52w](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w)

The image-only policy in `.github/scan/trivy-image-ignore.rego` matches those
three IDs for the exact version, PURL and BOM reference, with an empty package
path and a verified Python base layer DiffID. A finding in a new layer remains
visible. Reinspect the wheel and installed files when updating the Python
base image or pip. The filesystem scan checks the development dependency
through `uv.lock`.
