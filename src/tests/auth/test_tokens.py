from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.exceptions import (
    AuthCredentialsInvalidError,
    RefreshExpiredAPIError,
    SignatureExpiredError,
)
from src.modules.auth.backends import APIAuthBackend, BaseAuthBackend
from src.modules.auth.tokens import (
    AuthTokenType,
    TokenPayload,
    decode_jwt,
    encode_jwt,
    issue_token_pair,
)
from src.tests.factories import make_user
from src.tests.mocks import MockUOW
from src.utils import utcnow


class TestJWT:
    def test_encode_decode_jwt__ok(self, app_settings) -> None:
        token, expired_at = encode_jwt(
            TokenPayload(user_id=1, session_id="session", token_type=AuthTokenType.ACCESS),
            settings=app_settings,
        )

        payload = decode_jwt(token, expected_type=AuthTokenType.ACCESS, settings=app_settings)

        assert payload["user_id"] == 1
        assert payload["session_id"] == "session"
        assert expired_at > utcnow()

    @pytest.mark.parametrize(
        ("token_type", "error"),
        [
            (AuthTokenType.ACCESS, SignatureExpiredError),
            (AuthTokenType.REFRESH, RefreshExpiredAPIError),
        ],
    )
    def test_decode_jwt__expired__fails(self, app_settings, token_type, error) -> None:
        token, _ = encode_jwt(
            TokenPayload(user_id=1, token_type=token_type),
            settings=app_settings,
            expires_in=-1,
        )

        with pytest.raises(error):
            decode_jwt(token, expected_type=token_type, settings=app_settings)

    def test_decode_jwt__unexpected_type__fails(self, app_settings) -> None:
        token, _ = encode_jwt(
            TokenPayload(user_id=1, token_type=AuthTokenType.REFRESH), settings=app_settings
        )

        with pytest.raises(AuthCredentialsInvalidError, match="Expected ACCESS token"):
            decode_jwt(token, expected_type=AuthTokenType.ACCESS, settings=app_settings)

    def test_issue_token_pair__creates_access_and_refresh(self, app_settings) -> None:
        tokens = issue_token_pair(user_id=1, session_id="session", settings=app_settings)

        assert (
            decode_jwt(
                tokens.access_token, expected_type=AuthTokenType.ACCESS, settings=app_settings
            )["session_id"]
            == "session"
        )
        assert (
            decode_jwt(
                tokens.refresh_token, expected_type=AuthTokenType.REFRESH, settings=app_settings
            )["session_id"]
            == "session"
        )


class TestAPIAuthBackend:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("x" * 64, True),
            ("x" * 63, False),
            ("x" * 64 + ".", False),
        ],
    )
    def test_seems_like_user_access_token(self, token: str, expected: bool) -> None:
        assert BaseAuthBackend._seems_like_user_access_token(token) is expected

    async def test_create_user_session__persists_issued_refresh_token(
        self, app_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = SimpleNamespace(create=AsyncMock())
        monkeypatch.setattr("src.modules.auth.backends.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.backends.UserSessionRepository", Mock(return_value=repository)
        )

        tokens = await APIAuthBackend(SimpleNamespace(), settings=app_settings).create_user_session(
            make_user(id=7)
        )

        assert tokens.access_token
        assert tokens.refresh_token
        assert repository.create.await_args.kwargs["user_id"] == 7
        assert repository.create.await_args.kwargs["refresh_token"] == tokens.refresh_token

    async def test_refresh_user_session__rotates_tokens(
        self, app_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        auth = SimpleNamespace(
            user=make_user(id=7), session=SimpleNamespace(id=3, public_id="session")
        )
        repository = SimpleNamespace(get=AsyncMock(return_value=object()), update=AsyncMock())
        backend = APIAuthBackend(SimpleNamespace(), settings=app_settings)
        monkeypatch.setattr(backend, "_authenticate_refresh_token", AsyncMock(return_value=auth))
        monkeypatch.setattr("src.modules.auth.backends.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.backends.UserSessionRepository", Mock(return_value=repository)
        )

        tokens = await backend.refresh_user_session("refresh-token")

        assert tokens.access_token
        repository.get.assert_awaited_once_with(3)
        assert repository.update.await_args.kwargs["refresh_token"] == tokens.refresh_token
