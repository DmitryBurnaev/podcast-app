from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


class TestAuthSignInAPI:
    url = "/api/auth/sign-in/"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"email": "not-an-email", "password": "secret"},
            {"email": "user@podcast.dev", "password": ""},
        ],
    )
    def test_sign_in__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, str],
    ) -> None:
        response = client.post(self.url, json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )

    def test_sign_in__unknown_user__fail(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_repository = SimpleNamespace(get_by_email=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: user_repository)

        response = client.post(
            self.url,
            json={"email": "user@podcast.dev", "password": "secret"},
        )

        assert_error_response(
            response,
            status_code=401,
            code="AUTH_INVALID",
            message="Authentication credentials are invalid.",
        )
        user_repository.get_by_email.assert_awaited_once_with("user@podcast.dev")
