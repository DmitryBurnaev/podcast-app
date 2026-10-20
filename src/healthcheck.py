"""Container readiness probes for the web application and this container's RQ worker."""

import asyncio
import json
import os
import socket
import sys
from datetime import UTC, datetime
from urllib.request import urlopen

from redis import Redis
from rq import Worker

from src.modules.db import verify_database_reachable
from src.settings.app import get_app_settings


def check_web() -> None:
    """Require an HTTP-ready app, Redis, and a reachable PostgreSQL database."""
    port = int(os.environ.get("APP_PORT", "8000"))
    with urlopen(f"http://127.0.0.1:{port}/api/system/health/", timeout=3) as response:
        if response.status != 200 or json.load(response).get("status") != "ok":
            raise RuntimeError("Web health endpoint did not return status=ok")
    asyncio.run(verify_database_reachable())


def check_worker() -> None:
    """A worker on another container or a stale registration is not readiness."""
    settings = get_app_settings()
    with Redis(
        host=settings.redis.host,
        port=settings.redis.port,
        db=settings.redis.db,
        socket_connect_timeout=3,
        socket_timeout=3,
    ) as connection:
        for worker in Worker.all(connection=connection):
            if (
                worker.hostname == socket.gethostname()
                and settings.rq_queue_name in worker.queue_names()
                and worker.last_heartbeat is not None
                and worker.get_state() in {"idle", "busy"}
                # Idle RQ workers can block until worker_ttl - 15 before a heartbeat.
                and (datetime.now(UTC) - worker.last_heartbeat).total_seconds()
                < worker.worker_ttl + 60
            ):
                return
    raise RuntimeError("No live RQ worker for this container and queue")


def main() -> int:
    try:
        role = sys.argv[1] if len(sys.argv) > 1 else ""
        if role == "web":
            check_web()
        elif role == "background":
            check_worker()
        else:
            raise ValueError("Usage: python -m src.healthcheck web|background")
    except Exception as exc:
        print(f"Readiness check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
