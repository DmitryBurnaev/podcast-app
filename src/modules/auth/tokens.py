import dataclasses
import datetime
import logging
from enum import StrEnum
from typing import Any, NamedTuple

import jwt

from src.exceptions import (
    RefreshExpiredAPIError,
    SignatureExpiredError,
    AuthCredentialsInvalidError,
)
from src.settings.app import AppSettings, get_app_settings
from src.utils import utcnow
from src.modules.auth.types import TokenData
from src.modules.db.models.users import User, UserSession

logger = logging.getLogger(__name__)
LENGTH_USER_ACCESS_TOKEN = 64


class AuthTokenType(StrEnum):
    ACCESS = "ACCESS"
    REFRESH = "REFRESH"
    RESET_PASSWORD = "RESET_PASSWORD"
    USER_ACCESS = "USER_ACCESS"
    COOKIE = "COOKIE"


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
    payload: TokenData
    refresh_token: str


@dataclasses.dataclass
class TokenPayload:
    user_id: int
    session_id: str | None = None
    token_type: AuthTokenType = AuthTokenType.ACCESS
    exp: datetime.datetime | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        """Return the JWT-serializable payload dictionary."""
        data = dataclasses.asdict(self)
        data["token_type"] = str(self.token_type)
        return data


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
    token = jwt.encode(
        payload.as_dict(),
        key=settings.app_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expired_at


def decode_jwt(token: str, expected_type: AuthTokenType, settings: AppSettings) -> TokenData:
    """
    Decodes JWT token and returns decoded data

    :param token: encoded JWT
    :param expected_type: expected token type
    :param settings: current app's settings
    :raises Errors, based on PyJWTError
    :return: decoded and pre-validated data (see `modules.auth.types.TokenData` for details)
    """
    try:
        payload = jwt.decode(
            token,
            key=settings.app_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        if expected_type == AuthTokenType.REFRESH:
            raise RefreshExpiredAPIError() from exc

        raise SignatureExpiredError() from exc

    except jwt.InvalidTokenError as exc:
        raise AuthCredentialsInvalidError(details=str(exc)) from exc

    token_type = str(payload.get("token_type", "")).upper()
    if token_type != expected_type.value:
        raise AuthCredentialsInvalidError(
            details=f"Expected {expected_type.value} token, got {token_type or 'unknown'}."
        )

    try:
        exp_iso: int | None = payload.get("exp") or 0
        if not exp_iso:
            raise ValueError("Missing expiration time")

        _user_id: str | None = payload.get("user_id")
        user_id: int | None = int(_user_id) if _user_id else None
        if user_id is None:
            raise ValueError("Missing user id")

        session_id: str = payload.get("session_id") or ""
        if not session_id:
            raise ValueError("Missing session id")

        exp = datetime.datetime.fromtimestamp(exp_iso, tz=datetime.timezone.utc)
        token_data = TokenData(
            token_type=expected_type.value,
            exp=exp,
            exp_iso=exp.isoformat(),
            user_id=user_id,
            session_id=session_id,
        )

    except Exception as exc:
        raise AuthCredentialsInvalidError(details=str(exc)) from exc

    return token_data


def issue_token_pair(user_id: int, session_id: str, settings: AppSettings) -> TokenCollection:
    """
    Prepare collection: refresh + access tokens (and expirations)

    :param user_id: current user in requested context
    :param session_id: user's session id (usually - uuid)
    :param settings: current app's settings
    :return given collection (access + refresh tokens)
    """
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
