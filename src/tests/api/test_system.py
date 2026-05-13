from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp


class TestSystemAPI:
    def test_info__ok(self, client: TestClient[PodcastApp]) -> None:
        response = client.get("/api/system/info/")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "vendors": ["test"]}

    def test_health__ok(
        self,
        client: TestClient[PodcastApp],
        mocked_redis_health: AsyncMock,
    ) -> None:
        response = client.get("/api/system/health/")

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["status"] == "ok"
        assert datetime.fromisoformat(response_data["timestamp"])
        mocked_redis_health.assert_awaited_once_with()


class TestAPIAuthGate:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/me/",
            "/api/podcasts/",
        ],
    )
    def test_protected_api__without_credentials__fail(
        self,
        auth_required_client: TestClient[PodcastApp],
        path: str,
    ) -> None:
        from src.tests.helpers import assert_error_response

        response = auth_required_client.get(path)

        assert_error_response(
            response,
            status_code=401,
            code="AUTH_MISSING",
            message="Authentication credentials were not provided.",
        )
