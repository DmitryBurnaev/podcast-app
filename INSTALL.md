# Podcast App installation guide

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

Create the PostgreSQL database and application role beforehand. For example,
connect with your PostgreSQL administrator account:

```sh
docker exec -it YOUR_POSTGRES_CONTAINER psql -U YOUR_POSTGRES_ADMIN -d postgres
```

Then run these commands in psql, using a new database/role name when needed:

```sql
CREATE ROLE podcasts LOGIN;
\password podcasts
CREATE DATABASE podcasts OWNER podcasts;
```

Store that role's password as `DB_PASSWORD` in the app configuration. The role
must own its database/schema and be able to apply Alembic migrations. Production
does not need the test suite's `CREATEDB` permission. Use an app-specific Redis
database if the Redis instance is shared. Redis must be reachable without
authentication, as required by the application's current connection settings;
do not publish it to an untrusted network.

## Service installation

### 1. Prepare the service account

Commands below use `/opt/podcast-app`, user/group `podcast-app-srv`, and unit
`podcast-app.service`. Run the administrative commands with sudo:

```sh
sudo useradd --system --user-group --create-home --home-dir /opt/podcast-app --shell /bin/bash podcast-app-srv
sudo usermod -aG docker podcast-app-srv
sudo install -d -o podcast-app-srv -g podcast-app-srv -m 750 /opt/podcast-app
```

### 2. Download and extract a release

Choose the required release tag. For a publicly downloadable GitHub Release:

```sh
VERSION=1.2.3 # replace with the desired release tag
curl --fail --location \
  "https://github.com/DmitryBurnaev/podcast-app/releases/download/${VERSION}/podcast-app-deploy.tar.gz" \
  --output /tmp/podcast-app-deploy.tar.gz
```

If the release requires sign-in, download it in your browser and transfer it to
`/tmp/podcast-app-deploy.tar.gz` on the server. Extract the selected kit and
create the private runtime configuration:

```sh
sudo -u podcast-app-srv tar -xzf /tmp/podcast-app-deploy.tar.gz -C /opt/podcast-app
sudo -u podcast-app-srv cp /opt/podcast-app/.env.template /opt/podcast-app/.env
sudo chmod 600 /opt/podcast-app/.env
sudoedit /opt/podcast-app/.env
```

### 3. Configure the environment

The kit contains the repository's single `.env.template`, with local-development
defaults. Set the following values in `/opt/podcast-app/.env` before deploying:

| Setting | Server value |
| --- | --- |
| `DB_HOST`, `DB_PORT` | Existing PostgreSQL alias on `storage`, such as `podcast-db`, and internal port `5432` |
| `DB_USER`, `DB_NAME`, `DB_PASSWORD` | Existing application role/database and its real password |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | Existing Redis alias, such as `podcast-redis`, internal port `6379`, and the selected app database |
| `APP_SECRET_KEY` | A unique random secret; use at least 64 ASCII characters |
| `SENS_DATA_ENCRYPT_KEY` | A unique random key of at least 32 ASCII characters; preserve it across upgrades |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Initial admin credentials with a unique password |
| `S3_STORAGE_URL`, `S3_REGION_NAME`, `S3_BUCKET_NAME` | Real endpoint, region, and existing bucket |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Credentials for the configured bucket |
| `SITE_URL`, `SERVICE_URL` | Your public HTTPS origin, for example `https://podcast.example.com` |
| `AUTH_COOKIE_SECURE` | `true` for production HTTPS |
| `API_DOCS_ENABLED` | `false` unless the API documentation should be exposed |
| `FORWARDED_ALLOW_IPS` | `*` for the trusted host Nginx topology described below |
| `APP_PORT` | `8003` by default; match the Nginx upstream if changed |

Remove the local `DOCKER_IMAGE` entry; the release's `.version` selects it.
Generate new application/admin secrets with `openssl rand -hex 32` and copy them
into the corresponding fields. Use the existing database and S3 credentials
rather than generating replacements. The release kit needs no Python or uv on
the host; `make secrets` is for a source checkout and is described in README.

The encryption key must survive upgrades or previously encrypted data will
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

### 4. Configure image access and systemd

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

### 5. Start and verify the release

Start the selected release (the deployment script pulls the images first):

```sh
sudo -iu podcast-app-srv /opt/podcast-app/bin/deploy
sudo -iu podcast-app-srv /opt/podcast-app/bin/service health
curl --fail http://127.0.0.1:8003/api/system/health/
```

The unit waits for both services, rather than treating process creation as
successful startup. Web applies migrations before starting Uvicorn. The worker
starts after web readiness. A failed migration, dependency check, image pull,
or readiness timeout makes deployment fail. The default readiness timeout is
240 seconds, within systemd's 360-second startup limit.

### 6. Configure Nginx and HTTPS

For Debian/Ubuntu with host Nginx installed:

```sh
sudo install -m 644 /opt/podcast-app/nginx.conf /etc/nginx/sites-available/podcast-app.conf
sudoedit /etc/nginx/sites-available/podcast-app.conf
sudo ln -s /etc/nginx/sites-available/podcast-app.conf /etc/nginx/sites-enabled/podcast-app.conf
sudo nginx -t
sudo systemctl reload nginx
```

Set your domain and match the upstream port to `APP_PORT` (default `8003`)
before validating/reloading. On other distributions, use their Nginx include
layout. Configure a certificate, HTTPS listener and renewal using your host's
TLS tooling before exposing the public site.

The app binds only to host loopback. The example proxies all paths so the UI,
static files, cookie-authenticated browser APIs, admin, and public RSS/media
links remain accessible. Authentication stays in the application.
`AUTH_COOKIE_SECURE=true` requires HTTPS. For an isolated HTTP smoke test only,
set it to `false`, then restore it before exposing the service. Set both public
URLs to your HTTPS origin. `FORWARDED_ALLOW_IPS=*` assumes access is restricted
to the host proxy and trusted containers on `storage`.

Use `/padm/` and the configured admin credentials to create the first ordinary
user; configure SMTP if your onboarding uses invitation email.

## Service management

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

## Upgrades and rollback

Before upgrading, back up PostgreSQL and preserve `.env` and the previous
`.version`. Extract the new kit into `/opt/podcast-app` as the service account;
it replaces `.version`, scripts, and examples but contains no `.env`. Reinstall
the systemd unit and run `daemon-reload` if the unit changed. Then run
`bin/deploy`. An image-pull failure leaves the running release untouched.
The scripts never prune host images, delete volumes, or remove the external
network. Restore the previous image/version and deploy again for an application
rollback only when its database schema is compatible; a database backup is
needed to reverse incompatible migrations.

## Automatic deployment

Set repository variable `DEPLOY_ENABLED=true` after preparing the server. The
`production` GitHub environment can require deployment approval. CI uses the
same release kit and management scripts as manual deployment.

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

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `storage` network is missing | Create it and attach both dependency containers with the aliases used in `.env` |
| Migrations or database startup fail | Database name, role/password, network alias, ownership, and `bin/service logs web` |
| Web starts but worker is unhealthy | `bin/service logs background`, Redis settings, and RQ worker registration |
| Login redirects back to login | Public URL scheme, HTTPS configuration, and `AUTH_COOKIE_SECURE` |
| Pull or SSH deployment fails | Registry access for the service account, verified SSH host key, and the documented sudoers rules |
| systemd startup fails | `journalctl -u podcast-app.service -n 100 --no-pager` and `bin/service logs --tail 100` |

## Server acceptance checklist

On a disposable Linux server, verify first installation, reboot startup, an
upgrade, and start/stop/restart/status/logs/health. Log in, create a podcast and an
episode from a small permitted media source, wait for the background job, and
verify S3 objects plus RSS and audio playback/download. Repeat an update and
confirm configuration and data survive. Also test a failed image pull, invalid
database settings, and a migration failure: each must fail deployment with
diagnostics. These server checks require real configured dependencies and are
separate from the mocked external boundaries in ordinary automated tests.
