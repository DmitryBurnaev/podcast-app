import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from jwt import InvalidTokenError
from litestar import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.repositories import UserIPRepository
from src.settings.app import AppSettings
from src.utils import hash_string, utcnow
from src.modules.auth.constants import AuthTokenType

logger = logging.getLogger(__name__)


@dataclass
class TokenCollection:
    refresh_token: str
    refresh_token_expired_at: datetime
    access_token: str
    access_token_expired_at: datetime


def encode_jwt(
    payload: dict,
    settings: AppSettings,
    token_type: AuthTokenType = AuthTokenType.ACCESS,
    expires_in: int | None = None,
) -> tuple[str, datetime]:
    """Allows to prepare JWT for auth engine"""

    if token_type == AuthTokenType.REFRESH:
        expires_in = settings.jwt_refresh_expires_in
    else:
        expires_in = expires_in or settings.jwt_expires_in

    expired_at = utcnow() + timedelta(seconds=expires_in)
    payload["exp"] = expired_at
    payload["exp_iso"] = expired_at.isoformat()
    payload["token_type"] = str(token_type).lower()
    token = jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)
    return token, expired_at


def decode_jwt(encoded_jwt: str, settings: AppSettings) -> dict:
    """Allows to decode received JWT token to payload"""
    parts_count = len(encoded_jwt.split("."))
    if parts_count != 3:
        raise InvalidTokenError("Not enough segments")

    return jwt.decode(encoded_jwt, settings.app_secret_key, algorithms=[settings.jwt_algorithm])


def extract_ip_address(request: Request, settings: AppSettings) -> str | None:
    """Find IP address from request Headers"""

    if ip_address := request.headers.get(settings.request_ip_header):
        return ip_address

    if settings.flags.debug_mode:
        return settings.default_request_user_ip

    user_id = request.user.id if "user" in request.scope else "Unknown"
    logger.warning(
        "Not found ip-header (%s) for user: %s | headers: %s",
        settings.request_ip_header,
        user_id,
        request.headers,
    )
    return None


async def register_ip(request: Request, db_session: AsyncSession, settings: AppSettings) -> None:
    """Allows registration new IP for requested user"""

    logger.debug(
        "Requested register IP from: user: %i | headers: %s", request.user.id, request.headers
    )
    if not (ip_address := extract_ip_address(request, settings)):
        return

    user_ip_data = {"user_id": request.user.id, "hashed_address": hash_string(ip_address)}
    user_ip_repo = UserIPRepository(db_session)
    user_ip = await user_ip_repo.first(**user_ip_data)
    if user_ip:
        logger.debug("Found UserIP record for: %s | ip: %s", user_ip_data, ip_address)
    else:
        await user_ip_repo.create(**user_ip_data)
        logger.debug("Created NEW UserIP record for: %s | ip: %s", user_ip_data, ip_address)
