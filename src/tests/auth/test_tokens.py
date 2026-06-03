from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from exceptions import (
    AuthMissingAPIError,
    AuthInvalidAPIError,
    TokenExpiredAPIError,
    RefreshExpiredAPIError,
    SessionInactiveAPIError,
)
from src.modules.auth.tokens import (
    AuthTokenType,
    AuthenticatedRequest,
    TokenPayload,
    _seems_like_user_access_token,
    authenticate_bearer_request,
    authenticate_refresh_token,
    create_user_session,
    decode_jwt,
    encode_jwt,
    extract_bearer_token,
    issue_token_pair,
    refresh_user_session,
)
from src.modules.db.models.users import LENGTH_USER_ACCESS_TOKEN
from src.tests.factories import make_user
from src.tests.mocks import MockUOW
from src.utils import utcnow


def _base_repository_for(repository: object) -> type:
    class BaseRepositoryForTest:
        @classmethod
        def __class_getitem__(cls, item: object) -> type["BaseRepositoryForTest"]:
            return cls

        def __new__(cls, *args: object, **kwargs: object) -> object:
            return repository

    return BaseRepositoryForTest


class TestTokenPayload:
    def test_as_dict__serializes_token_type_as_string(self) -> None:
        payload = TokenPayload(user_id=1, session_id="session", token_type=AuthTokenType.REFRESH)

        assert payload.as_dict()["token_type"] == "REFRESH"


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

    def test_decode_jwt__expired_access__fail(self, app_settings) -> None:
        token, _ = encode_jwt(
            TokenPayload(user_id=1, token_type=AuthTokenType.ACCESS),
            settings=app_settings,
            expires_in=-1,
        )

        with pytest.raises(TokenExpiredAPIError):
            decode_jwt(token, expected_type=AuthTokenType.ACCESS, settings=app_settings)

    def test_decode_jwt__expired_refresh__fail(self, app_settings) -> None:
        token, _ = encode_jwt(
            TokenPayload(user_id=1, token_type=AuthTokenType.REFRESH),
            settings=app_settings,
            expires_in=-1,
        )

        with pytest.raises(RefreshExpiredAPIError):
            decode_jwt(token, expected_type=AuthTokenType.REFRESH, settings=app_settings)

    def test_decode_jwt__invalid_token__fail(self, app_settings) -> None:
        with pytest.raises(AuthInvalidAPIError):
            decode_jwt("not-a-token", expected_type=AuthTokenType.ACCESS, settings=app_settings)

    def test_decode_jwt__unexpected_type__fail(self, app_settings) -> None:
        token, _ = encode_jwt(
            TokenPayload(user_id=1, token_type=AuthTokenType.REFRESH),
            settings=app_settings,
        )

        with pytest.raises(AuthInvalidAPIError, match="Expected ACCESS token"):
            decode_jwt(token, expected_type=AuthTokenType.ACCESS, settings=app_settings)

    def test_issue_token_pair__creates_access_and_refresh(self, app_settings) -> None:
        tokens = issue_token_pair(user_id=1, session_id="session", settings=app_settings)

        access_payload = decode_jwt(
            tokens.access_token,
            expected_type=AuthTokenType.ACCESS,
            settings=app_settings,
        )
        refresh_payload = decode_jwt(
            tokens.refresh_token,
            expected_type=AuthTokenType.REFRESH,
            settings=app_settings,
        )

        assert access_payload["session_id"] == "session"
        assert refresh_payload["session_id"] == "session"


class TestBearerToken:
    @pytest.mark.parametrize("headers", [{}, {"Authorization": ""}])
    def test_extract_bearer_token__missing__fail(self, headers: dict[str, str]) -> None:
        request = SimpleNamespace(headers=headers)

        with pytest.raises(AuthMissingAPIError):
            extract_bearer_token(request)

    @pytest.mark.parametrize(
        "header",
        [
            "Token abc",
            "Bearer",
            "Bearer one two",
        ],
    )
    def test_extract_bearer_token__invalid__fail(self, header: str) -> None:
        request = SimpleNamespace(headers={"Authorization": header})

        with pytest.raises(AuthInvalidAPIError):
            extract_bearer_token(request)

    def test_extract_bearer_token__ok(self) -> None:
        request = SimpleNamespace(headers={"authorization": "Bearer token"})

        assert extract_bearer_token(request) == "token"

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("x" * LENGTH_USER_ACCESS_TOKEN, True),
            ("x" * (LENGTH_USER_ACCESS_TOKEN - 1), False),
            ("x" * LENGTH_USER_ACCESS_TOKEN + ".", False),
        ],
    )
    def test_seems_like_user_access_token__ok(self, token: str, expected: bool) -> None:
        assert _seems_like_user_access_token(token) is expected


class TestSessionTokens:
    async def test_create_user_session__creates_session(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        uow = MockUOW()
        session_repository = SimpleNamespace(create=AsyncMock())
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: uow)
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )
        user = make_user(id=7)

        tokens = await create_user_session(user, settings=app_settings)

        assert tokens.access_token
        session_repository.create.assert_awaited_once()
        assert session_repository.create.await_args.kwargs["user_id"] == 7
        assert session_repository.create.await_args.kwargs["is_active"] is True
        uow.mark_for_commit.assert_called_once_with()

    async def test_authenticate_bearer_request__jwt_ok(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = make_user(id=7)
        user_session = SimpleNamespace(public_id="session", user_id=7)
        session_repository = SimpleNamespace(
            get_active_with_user=AsyncMock(return_value=(user_session, user))
        )
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )
        token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id="session", token_type=AuthTokenType.ACCESS),
            settings=app_settings,
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

        result = await authenticate_bearer_request(request, settings=app_settings)

        assert result.user is user
        assert result.session_id == "session"
        session_repository.get_active_with_user.assert_awaited_once_with("session")

    async def test_authenticate_bearer_request__missing_payload_fields__fail(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id=None, token_type=AuthTokenType.ACCESS),
            settings=app_settings,
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

        with pytest.raises(AuthInvalidAPIError, match="misses user_id or session_id"):
            await authenticate_bearer_request(request, settings=app_settings)

    async def test_authenticate_bearer_request__inactive_session__fail(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_repository = SimpleNamespace(get_active_with_user=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )
        token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id="session", token_type=AuthTokenType.ACCESS),
            settings=app_settings,
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

        with pytest.raises(SessionInactiveAPIError):
            await authenticate_bearer_request(request, settings=app_settings)

    @pytest.mark.parametrize(
        ("session_user_id", "user_active"),
        [(8, True), (7, False)],
    )
    async def test_authenticate_bearer_request__session_user_mismatch__fail(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
        session_user_id: int,
        user_active: bool,
    ) -> None:
        user = make_user(id=7, is_active=user_active)
        user_session = SimpleNamespace(public_id="session", user_id=session_user_id)
        session_repository = SimpleNamespace(
            get_active_with_user=AsyncMock(return_value=(user_session, user))
        )
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )
        token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id="session", token_type=AuthTokenType.ACCESS),
            settings=app_settings,
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

        with pytest.raises(AuthInvalidAPIError):
            await authenticate_bearer_request(request, settings=app_settings)

    async def test_authenticate_refresh_token__ok(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refresh_token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id="session", token_type=AuthTokenType.REFRESH),
            settings=app_settings,
        )
        user = make_user(id=7)
        user_session = SimpleNamespace(public_id="session", user_id=7, refresh_token=refresh_token)
        session_repository = SimpleNamespace(
            get_active_with_user=AsyncMock(return_value=(user_session, user))
        )
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )

        result = await authenticate_refresh_token(refresh_token, settings=app_settings)

        assert result.user is user
        assert result.session is user_session
        assert result.refresh_token == refresh_token

    async def test_authenticate_refresh_token__stored_token_mismatch__fail(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refresh_token, _ = encode_jwt(
            TokenPayload(user_id=7, session_id="session", token_type=AuthTokenType.REFRESH),
            settings=app_settings,
        )
        user_session = SimpleNamespace(public_id="session", user_id=7, refresh_token="another")
        session_repository = SimpleNamespace(
            get_active_with_user=AsyncMock(return_value=(user_session, make_user(id=7)))
        )
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )

        with pytest.raises(AuthInvalidAPIError, match="does not match"):
            await authenticate_refresh_token(refresh_token, settings=app_settings)

    async def test_refresh_user_session__updates_session(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        auth_session = SimpleNamespace(id=3, public_id="session")
        auth = SimpleNamespace(user=make_user(id=7), session=auth_session)
        stored_session = SimpleNamespace(id=3)
        session_repository = SimpleNamespace(
            get=AsyncMock(return_value=stored_session),
            update=AsyncMock(),
        )
        monkeypatch.setattr(
            "src.modules.auth.tokens.authenticate_refresh_token", AsyncMock(return_value=auth)
        )
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserSessionRepository",
            Mock(return_value=session_repository),
        )

        tokens = await refresh_user_session("refresh", settings=app_settings)

        assert tokens.refresh_token
        session_repository.get.assert_awaited_once_with(3)
        session_repository.update.assert_awaited_once()
        assert session_repository.update.await_args.kwargs["is_active"] is True


class TestUserAccessToken:
    async def test_authenticate_bearer_request__user_access_token_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw_token = "x" * LENGTH_USER_ACCESS_TOKEN
        access_token = SimpleNamespace(user_id=7, active=True)
        token_repository = SimpleNamespace(first=AsyncMock(return_value=access_token))
        user = make_user(id=7)
        user_repository = SimpleNamespace(first=AsyncMock(return_value=user))
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.BaseRepository",
            _base_repository_for(token_repository),
        )
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserRepository",
            Mock(return_value=user_repository),
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {raw_token}"})

        result = await authenticate_bearer_request(request, settings=SimpleNamespace())

        assert result == AuthenticatedRequest(
            user=user,
            session_id=None,
            payload={"user_id": 7, "token_type": AuthTokenType.USER_ACCESS.value},
        )
        token_repository.first.assert_awaited_once()
        user_repository.first.assert_awaited_once_with(id=7, is_active=True)

    @pytest.mark.parametrize(
        "access_token",
        [None, SimpleNamespace(user_id=7, active=False)],
    )
    async def test_authenticate_bearer_request__user_access_token_unknown__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        access_token: object,
    ) -> None:
        raw_token = "x" * LENGTH_USER_ACCESS_TOKEN
        token_repository = SimpleNamespace(first=AsyncMock(return_value=access_token))
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.BaseRepository",
            _base_repository_for(token_repository),
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {raw_token}"})

        with pytest.raises(AuthInvalidAPIError, match="unknown"):
            await authenticate_bearer_request(request, settings=SimpleNamespace())

    async def test_authenticate_bearer_request__user_access_token_owner_missing__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw_token = "x" * LENGTH_USER_ACCESS_TOKEN
        token_repository = SimpleNamespace(
            first=AsyncMock(return_value=SimpleNamespace(user_id=7, active=True))
        )
        user_repository = SimpleNamespace(first=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.auth.tokens.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.tokens.BaseRepository",
            _base_repository_for(token_repository),
        )
        monkeypatch.setattr(
            "src.modules.auth.tokens.UserRepository",
            Mock(return_value=user_repository),
        )
        request = SimpleNamespace(headers={"Authorization": f"Bearer {raw_token}"})

        with pytest.raises(AuthInvalidAPIError, match="owner"):
            await authenticate_bearer_request(request, settings=SimpleNamespace())
