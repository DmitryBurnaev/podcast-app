# Podcast App server installation

The release provides one image for `linux/amd64` and `linux/arm64` and a
`podcast-app-deploy.tar.gz` deployment kit. Docker selects the server's platform.
The kit contains production Compose, `.env.template`, `.version`, Nginx and
systemd examples, `bin/*`, and this guide. No source checkout or build is needed
on the server.

## Server prerequisites

- Linux with systemd, Bash, Docker Engine and Docker Compose v2.24 or newer.
- A separately managed PostgreSQL (17 recommended), Redis (7 recommended), and
  an S3-compatible bucket. The application does not provision or stop them.
- Host Nginx and HTTPS configured for your domain, and outbound access to GHCR,
  S3, and the media providers you use. SMTP is optional, for invitation/reset mail.
- Enough disk space in Docker's data directory and `/tmp` inside the containers
  for concurrent media processing. Media caches and processing files are
  disposable; durable application data lives in PostgreSQL and S3.

### Shared Docker network

The production network is named **`storage`**; it must already exist. On first
setup, create it if needed and attach your existing dependency containers:

```sh
docker network create storage
docker network connect --alias podcast-db storage YOUR_POSTGRES_CONTAINER
docker network connect --alias podcast-redis storage YOUR_REDIS_CONTAINER
```

Skip creation/connection commands if already configured. Prefer declaring this
external network and these aliases in the dependencies' own Compose files so
they survive container recreation. Set `DB_HOST=podcast-db` and
`REDIS_HOST=podcast-redis` in the app's `.env`, or use their existing network
aliases. Use container ports (`5432`/`6379` by default), not host-published ports.
`localhost` inside an app container is not the database or Redis container.

Create the PostgreSQL database and application role beforehand. The role must
own its database/schema and be able to apply Alembic migrations. Production
does not need the test suite's `CREATEDB` permission. Use an app-specific Redis
database if the Redis instance is shared. Redis must be reachable without
authentication, as required by the application's current connection settings;
do not publish it to an untrusted network.

## First installation

Commands below use `/opt/podcast-app`, user/group `podcast-app-srv`, and unit
`podcast-app.service`. Run the administrative commands with sudo:

```sh
sudo useradd --system --user-group --create-home --home-dir /opt/podcast-app --shell /bin/bash podcast-app-srv
sudo usermod -aG docker podcast-app-srv
sudo install -d -o podcast-app-srv -g podcast-app-srv -m 750 /opt/podcast-app
```

Download `podcast-app-deploy.tar.gz` from the desired GitHub Release, transfer it
to the server, then extract it as the service account:

```sh
sudo -u podcast-app-srv tar -xzf podcast-app-deploy.tar.gz -C /opt/podcast-app
sudo -u podcast-app-srv cp /opt/podcast-app/.env.template /opt/podcast-app/.env
sudo chmod 600 /opt/podcast-app/.env
sudoedit /opt/podcast-app/.env
```

Fill the empty required fields: `DB_PASSWORD`, `APP_SECRET_KEY`,
`ADMIN_PASSWORD`, `SENS_DATA_ENCRYPT_KEY`, `S3_ACCESS_KEY_ID`, and
`S3_SECRET_ACCESS_KEY`. Set the real S3 endpoint, region, bucket, database
settings, `SITE_URL`, and `SERVICE_URL`. Generate unique secrets, for example
with `openssl rand -hex 32`; the encryption key needs at least 32 ASCII
characters. Preserve it across updates or previously encrypted data will
become unreadable.

The bucket must exist and permit the configured account to read, write, and
delete objects. The endpoint used for presigned media/RSS URLs must also be
reachable by clients. Configure bucket CORS for your HTTPS site when required
by the browser's media access.

The kit's `.version` selects both `DOCKER_IMAGE` (the complete versioned GHCR
reference) and `DOCKER_IMAGE_TAG` (the application version). Management scripts
pass `.env` and then `.version` to Compose; `.version` takes precedence for image
selection, including over exported image variables. They do not execute dotenv
files as shell scripts. Do not put image selection in `.env`.

If the GHCR package requires authentication, log in as `podcast-app-srv` with a
token permitted to read the package before deploying. Ensure subsequent updates
use the same account's Docker authentication.

```sh
sudo install -m 644 /opt/podcast-app/podcast-app.service /etc/systemd/system/podcast-app.service
sudo systemctl daemon-reload
sudo systemctl enable podcast-app.service
```

Install a narrowly scoped sudoers rule for the service/deployment account using
`visudo`. Adjust executable paths to those on your distribution:

```sudoers
podcast-app-srv ALL=(root) NOPASSWD: /usr/bin/systemctl start podcast-app.service, /usr/bin/systemctl stop podcast-app.service, /usr/bin/systemctl restart podcast-app.service, /usr/bin/systemctl status podcast-app.service, /usr/bin/systemctl is-active --quiet podcast-app.service
```

Start the selected release (the deployment script pulls the images first):

```sh
sudo -iu podcast-app-srv /opt/podcast-app/bin/deploy
```

The unit waits for both services, rather than treating process creation as
successful startup. Web applies migrations before starting Uvicorn. The worker
starts after web readiness. A failed migration, dependency check, image pull,
or readiness timeout makes deployment fail. The default readiness timeout is
240 seconds, within systemd's 360-second startup limit.

### Reverse proxy and HTTPS

Copy `nginx.conf` into your host Nginx configuration, set your domain, match its
upstream port to `APP_PORT` (default `8003`), and configure TLS using your host's
certificate tooling. Validate with `nginx -t` before reloading Nginx.

The app binds only to host loopback. The example proxies all paths so the UI,
static files, cookie-authenticated browser APIs, admin, and public RSS/media
links remain accessible. Authentication stays in the application.
`AUTH_COOKIE_SECURE=true` requires HTTPS. For an isolated HTTP smoke test only,
set it to `false`, then restore it before exposing the service. Set both public
URLs to your HTTPS origin. `FORWARDED_ALLOW_IPS=*` assumes access is restricted
to the host proxy and trusted containers on `storage`.

Use `/padm/` and the configured admin credentials to create the first ordinary
user; configure SMTP if your onboarding uses invitation email.

## Operations and upgrades

The scripts work from any working directory:

```sh
/opt/podcast-app/bin/service status
/opt/podcast-app/bin/service health
/opt/podcast-app/bin/service logs --tail 100 --follow
/opt/podcast-app/bin/service logs web
/opt/podcast-app/bin/service restart
/opt/podcast-app/bin/service stop
/opt/podcast-app/bin/service start
```

Application logs come from `docker compose logs`; systemd lifecycle errors are
also available through `journalctl -u podcast-app.service`. Container restart
policies handle process exits; `service health` checks current web/database
readiness and this worker container's live RQ heartbeat. Docker marks a failed
probe unhealthy; it does not automatically restart an unhealthy running process.

Before upgrading, back up PostgreSQL and preserve `.env` and the previous
`.version`. Extract the new kit into `/opt/podcast-app` as the service account;
it replaces `.version`, scripts, and examples but contains no `.env`. Reinstall
the systemd unit and run `daemon-reload` if the unit changed. Then run
`bin/deploy`. An image-pull failure leaves the running release untouched.
The scripts never prune host images, delete volumes, or remove the external
network. Restore the previous image/version and deploy again for an application
rollback only when its database schema is compatible; a database backup is
needed to reverse incompatible migrations.

## CI/CD

- PRs targeting `main`: AMD64 image builds, Black/Ruff/mypy, backend tests, frontend
  JavaScript tests, and deployment script checks.
- Pushes to `main`: service and test image builds and backend tests on AMD64
  and ARM64; ARM64 executes under QEMU on the hosted AMD64 runner.
- Version tags matching `1.2.3`: the same checks on both platforms, then one
  multi-platform service image published to GHCR as the version and `latest`.
  Both manifests are checked before GitHub Release creation or deployment.
- Both platform jobs smoke-test migrations, web and worker readiness, and
  synthetic MP3 encoding/probing. S3 values in `.github/ci.env` are dummy values;
  this smoke test does not verify a real bucket or external media providers.
- Releases include the deployment kit and `release-metadata.json`.
- Set repository variable `DEPLOY_ENABLED=true` to enable automatic deployment
  after preparing the server. The `production` GitHub environment can impose
  approval rules. CI tests do not receive production secrets.

For automatic deployment configure `SSH_PKEY`, `SSH_HOST`, `SSH_USER`,
`SSH_PORT` (defaults to 22), `SSH_KNOWN_HOSTS` (verified host-key entries, including
the port for nonstandard ports), and `PROD_PROJECT_ROOT` (defaults to
`/opt/podcast-app`). The SSH user needs write access to that directory and Docker
access. Use the service account as the SSH user so manual and automated image
pulls share its registry authentication. In addition to the management commands,
automatic unit updates need these sudoers permissions:

```sudoers
podcast-app-srv ALL=(root) NOPASSWD: /usr/bin/install -m 644 podcast-app.service /etc/systemd/system/podcast-app.service, /usr/bin/systemctl daemon-reload
```

CI delivers the same kit and runs `bin/deploy`; it does not overwrite `.env`,
install Nginx configuration, or provision external dependencies.

## Verification

For local checks, use `.github/ci.env` explicitly; no production `.env` is needed.
The database fixture creates a separate `TEST_DB_NAME` database and needs a
CI-only PostgreSQL role with `CREATEDB`. Infrastructure data defaults to `.data`;
set `PODCAST_DATA_DIR` to a disposable absolute directory for an isolated run.

```sh
make test-frontend
node --test tests/deployment.test.cjs
APP_PLATFORM=linux/arm64 make build-platform
APP_PLATFORM=linux/arm64 make check-platform
```

Repeat the last two commands with `linux/amd64`. The platform check verifies the
actual loaded image architectures and media tools, starts PostgreSQL/Redis,
and runs the backend suite without rebuilding images. Tear down that test stack
with `docker compose --env-file .github/ci.env --profile test down`.

On a disposable Linux server, verify first installation, reboot startup, an
upgrade, and start/stop/restart/status/logs/health. Log in, create a podcast and an
episode from a small permitted media source, wait for the background job, and
verify S3 objects plus RSS and audio playback/download. Repeat an update and
confirm configuration and data survive. Also test a failed image pull, invalid
database settings, and a migration failure: each must fail deployment with
diagnostics. These server checks require real configured dependencies and are
separate from the mocked external boundaries in ordinary automated tests.
