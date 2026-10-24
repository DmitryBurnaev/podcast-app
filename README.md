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
- Node.js 22 or newer for the frontend/deployment script tests.

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
node --test tests/deployment.test.cjs
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
with `docker compose --env-file .github/ci.env --profile test down`. Running ARM64
images on AMD64 requires QEMU/binfmt support; the CI workflow configures it.

## CI/CD

- PRs targeting `main` (`tests.yaml`): AMD64 image builds, Black/Ruff/mypy,
  backend/frontend tests, and deployment script checks.
- Pushes to `main` (`tests-full.yaml`), including PR merges: service/test image
  builds and backend tests on AMD64 and ARM64; ARM64 runs under QEMU on the
  hosted AMD64 runner.
- Version tags matching `1.2.3`: the same checks on both platforms, then one
  multi-platform service image published to GHCR as the version and `latest`.
  Both manifests are checked before GitHub Release creation or deployment.
- Both platform jobs smoke-test migrations, web and worker readiness, and
  synthetic MP3 encoding/probing. S3 values in `.github/ci.env` are dummy values;
  this smoke test does not verify a real bucket or external media providers.
- Releases include the deployment kit and `release-metadata.json`.
- See [automatic deployment](INSTALL.md#automatic-deployment) for server
  configuration and SSH secrets. Ordinary CI checks receive no production secrets.
