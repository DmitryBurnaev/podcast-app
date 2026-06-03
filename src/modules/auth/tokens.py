import dataclasses
import datetime
import logging
from enum import StrEnum
from typing import Any, NamedTuple
from uuid import uuid4

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from litestar.connection import Request

from exceptions import (
    AuthMissingAPIError,
    AuthInvalidAPIError,
    TokenExpiredAPIError,
    RefreshExpiredAPIError,
    SessionInactiveAPIError,
)
from src.modules.db.models.users import LENGTH_USER_ACCESS_TOKEN, User, UserAccessToken, UserSession
from src.modules.db.repositories import UserRepository, UserSessionRepository, BaseRepository
from src.modules.db.services import SASessionUOW
from src.settings.app import AppSettings, get_app_settings
from src.utils import hash_string, utcnow

logger = logging.getLogger(__name__)


class AuthTokenType(StrEnum):
    ACCESS = "ACCESS"
    REFRESH = "REFRESH"
    RESET_PASSWORD = "RESET_PASSWORD"
    USER_ACCESS = "USER_ACCESS"


class TokenCollection(NamedTuple):
    refresh_token: str
    refresh_token_expired_at: datetime.datetime
    access_token: str
    access_token_expired_at: datetime.datetime


class AuthenticatedRequest(NamedTuple):
    user: User
    session_id: str | None
    payload: dict[str, Any]


class RefreshAuthentication(NamedTuple):
    user: User
    session: UserSession
    payload: dict[str, Any]
    refresh_token: str


@dataclasses.dataclass
class TokenPayload:
    user_id: int
    session_id: str | None = None
    token_type: AuthTokenType = AuthTokenType.ACCESS
    exp: datetime.datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JWT-serializable payload dictionary."""
        data = dataclasses.asdict(self)
        data["token_type"] = str(self.token_type)
        return data


def _jwt_key(settings: AppSettings) -> str:
    return settings.app_secret_key.get_secret_value()


def encode_jwt(
    payload: TokenPayload,
    settings: AppSettings,
    expires_in: int | None = None,
) -> tuple[str, datetime.datetime]:
    """
    Prepares JWT token and returns it expires time

    :param payload: data which should be encoded
    :param settings: current app's settings
    :param expires_in: expiration time
    :return: encoded JWT, expires time
    """
    if expires_in is None:
        expires_in = (
            settings.jwt_refresh_expires_in
            if payload.token_type == AuthTokenType.REFRESH
            else settings.jwt_expires_in
        )

    expired_at = utcnow() + datetime.timedelta(seconds=(expires_in or 0))
    payload.exp = expired_at
    token = jwt.encode(payload.as_dict(), _jwt_key(settings), algorithm=settings.jwt_algorithm)
    return token, expired_at


def decode_jwt(
    token: str,
    *,
    expected_type: AuthTokenType,
    settings: AppSettings,
) -> dict[str, Any]:
    """
    Decodes JWT token and returns decoded data

    :param token: encoded JWT
    :param expected_type: expected token type
    :param settings: current app's settings
    :raises Errors, based on PyJWTError
    :return: decoded data
    """
    try:
        payload = jwt.decode(token, _jwt_key(settings), algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError as exc:
        if expected_type == AuthTokenType.REFRESH:
            raise RefreshExpiredAPIError() from exc

        raise TokenExpiredAPIError() from exc

    except InvalidTokenError as exc:
        raise AuthInvalidAPIError(details=str(exc)) from exc

    token_type = str(payload.get("token_type", "")).upper()
    if token_type != expected_type.value:
        raise AuthInvalidAPIError(
            details=f"Expected {expected_type.value} token, got {token_type or 'unknown'}."
        )

    return payload


def issue_token_pair(user_id: int, session_id: str, settings: AppSettings) -> TokenCollection:
    """Prepare collection: refresh + access tokens (and expirations)"""
    settings = settings or get_app_settings()
    access_token, access_exp = encode_jwt(
        TokenPayload(
            user_id=user_id,
            session_id=session_id,
            token_type=AuthTokenType.ACCESS,
        ),
        settings=settings,
    )
    refresh_token, refresh_exp = encode_jwt(
        TokenPayload(
            user_id=user_id,
            session_id=session_id,
            token_type=AuthTokenType.REFRESH,
        ),
        settings=settings,
    )
    return TokenCollection(
        refresh_token=refresh_token,
        refresh_token_expired_at=refresh_exp,
        access_token=access_token,
        access_token_expired_at=access_exp,
    )


async def create_user_session(user: User, settings: AppSettings) -> TokenCollection:
    session_id = str(uuid4())
    tokens = issue_token_pair(user_id=user.id, session_id=session_id, settings=settings)
    async with SASessionUOW() as uow:
        session_repo = UserSessionRepository(uow.session)
        await session_repo.create(
            public_id=session_id,
            user_id=user.id,
            refresh_token=tokens.refresh_token,
            is_active=True,
            expired_at=tokens.refresh_token_expired_at,
            created_at=utcnow(),
            refreshed_at=utcnow(),
        )
        uow.mark_for_commit()

    return tokens


def extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth_header:
        raise AuthMissingAPIError()

    parts = auth_header.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise AuthInvalidAPIError(details="Authorization header must be 'Bearer <token>'.")

    return parts[1]


async def authenticate_bearer_request(
    request: Request,
    settings: AppSettings,
) -> AuthenticatedRequest:
    token = extract_bearer_token(request)
    async with SASessionUOW() as uow:
        if _seems_like_user_access_token(token):
            return await _authenticate_user_access_token(uow, token)

        payload = decode_jwt(token, expected_type=AuthTokenType.ACCESS, settings=settings)
        user_id = int(payload.get("user_id") or 0)
        session_id = payload.get("session_id")
        if not user_id or not session_id:
            raise AuthInvalidAPIError(details="Token payload misses user_id or session_id.")

        session_repo = UserSessionRepository(uow.session)
        pair = await session_repo.get_active_with_user(str(session_id))
        if pair is None:
            raise SessionInactiveAPIError()

        user_session, user = pair
        if user_session.user_id != user_id or not user.is_active:
            raise AuthInvalidAPIError()

        return AuthenticatedRequest(user=user, session_id=str(session_id), payload=payload)


async def authenticate_refresh_token(
    refresh_token: str, settings: AppSettings
) -> RefreshAuthentication:
    payload = decode_jwt(refresh_token, expected_type=AuthTokenType.REFRESH, settings=settings)
    user_id = int(payload.get("user_id") or 0)
    session_id = payload.get("session_id")
    if not user_id or not session_id:
        raise AuthInvalidAPIError(details="Refresh token payload misses user_id or session_id.")

    async with SASessionUOW() as uow:
        session_repo = UserSessionRepository(uow.session)
        pair = await session_repo.get_active_with_user(str(session_id))
        if pair is None:
            raise SessionInactiveAPIError()

        user_session, user = pair
        if user_session.user_id != user_id or not user.is_active:
            raise AuthInvalidAPIError()

        if user_session.refresh_token != refresh_token:
            raise AuthInvalidAPIError(details="Refresh token does not match the session.")

        return RefreshAuthentication(
            user=user,
            session=user_session,
            payload=payload,
            refresh_token=refresh_token,
        )


async def refresh_user_session(refresh_token: str, settings: AppSettings) -> TokenCollection:
    auth = await authenticate_refresh_token(refresh_token, settings)
    tokens = issue_token_pair(
        user_id=auth.user.id,
        session_id=auth.session.public_id,
        settings=settings,
    )
    async with SASessionUOW() as uow:
        session_repo = UserSessionRepository(uow.session)
        user_session = await session_repo.get(auth.session.id)
        await session_repo.update(
            user_session,
            refresh_token=tokens.refresh_token,
            expired_at=tokens.refresh_token_expired_at,
            refreshed_at=utcnow(),
            is_active=True,
        )
        uow.mark_for_commit()

    return tokens


async def _authenticate_user_access_token(uow: SASessionUOW, token: str) -> AuthenticatedRequest:
    token_repo: BaseRepository[UserAccessToken] = BaseRepository[UserAccessToken](uow.session)
    token_repo.model = UserAccessToken
    access_token = await token_repo.first(token=hash_string(token))
    if access_token is None or not access_token.active:
        raise AuthInvalidAPIError(details="Provided access token is unknown, disabled, or expired.")

    user_repo = UserRepository(uow.session)
    user = await user_repo.first(id=access_token.user_id, is_active=True)
    if user is None:
        raise AuthInvalidAPIError(details="Access token owner is inactive or missing.")

    return AuthenticatedRequest(
        user=user,
        session_id=None,
        payload={"user_id": user.id, "token_type": AuthTokenType.USER_ACCESS.value},
    )


def _seems_like_user_access_token(token: str) -> bool:
    return len(token) == LENGTH_USER_ACCESS_TOKEN and "." not in token
