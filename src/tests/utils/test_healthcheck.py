"""Readiness must identify a live local worker and reject unhealthy HTTP responses."""

import io
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, create_autospec

import pytest
from redis import Redis
from rq import Worker

from src import healthcheck


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Worker:
    connection = Redis(socket_timeout=3)
    monkeypatch.setattr(connection, "hset", create_autospec(connection.hset, return_value=1))
    worker = Worker(["podcast"], connection=connection, prepare_for_work=False)
    worker.hostname = socket.gethostname()
    worker.last_heartbeat = datetime.now(UTC)
    worker.set_state("idle")
    monkeypatch.setattr(healthcheck, "Redis", lambda **kwargs: connection)
    monkeypatch.setattr(healthcheck.Worker, "all", lambda **kwargs: [worker])
    return worker


class TestWorkerReadiness:
    @pytest.mark.parametrize("state", ["idle", "busy"])
    @pytest.mark.parametrize("heartbeat_age", [0, 180])
    def test_accepts_a_live_local_worker(
        self, worker: Worker, state: str, heartbeat_age: int
    ) -> None:
        worker.set_state(state)
        worker.last_heartbeat = datetime.now(UTC) - timedelta(seconds=heartbeat_age)
        healthcheck.check_worker()

    def test_rejects_another_containers_worker(self, worker: Worker) -> None:
        worker.hostname = "another-container"
        with pytest.raises(RuntimeError, match="No live RQ worker"):
            healthcheck.check_worker()

    def test_rejects_a_stale_heartbeat(self, worker: Worker) -> None:
        worker.last_heartbeat = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(RuntimeError, match="No live RQ worker"):
            healthcheck.check_worker()

    def test_rejects_a_stopped_worker(self, worker: Worker) -> None:
        worker.set_state("stopped")
        with pytest.raises(RuntimeError, match="No live RQ worker"):
            healthcheck.check_worker()

    def test_rejects_a_worker_for_another_queue(
        self, worker: Worker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(worker, "queue_names", lambda: ["other-queue"])
        with pytest.raises(RuntimeError, match="No live RQ worker"):
            healthcheck.check_worker()


class HealthResponse(io.BytesIO):
    status = 200


class TestWebReadiness:
    @pytest.mark.parametrize("payload", [b'{"status":"error"}', b"{}", b"not-json"])
    def test_rejects_an_unhealthy_response(
        self, payload: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(healthcheck, "urlopen", lambda *args, **kwargs: HealthResponse(payload))
        with pytest.raises((RuntimeError, ValueError)):
            healthcheck.check_web()

    def test_requires_database_reachability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            healthcheck, "urlopen", lambda *args, **kwargs: HealthResponse(b'{"status":"ok"}')
        )
        database_probe = AsyncMock(side_effect=RuntimeError("database unavailable"))
        monkeypatch.setattr(healthcheck, "verify_database_reachable", database_probe)
        with pytest.raises(RuntimeError, match="database unavailable"):
            healthcheck.check_web()

    def test_accepts_http_and_database_readiness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            healthcheck, "urlopen", lambda *args, **kwargs: HealthResponse(b'{"status":"ok"}')
        )
        database_probe = AsyncMock()
        monkeypatch.setattr(healthcheck, "verify_database_reachable", database_probe)
        healthcheck.check_web()
        database_probe.assert_awaited_once()
