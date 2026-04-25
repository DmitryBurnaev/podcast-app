"""Resolve browser session cookie to ORM User on each HTTP request."""

import logging
from typing import TYPE_CHECKING
from litestar.connection import Request

from src.modules.db.models.users import User
from src.modules.db.repositories import UserSessionRepository
from src.modules.db.services import SASessionUOW

if TYPE_CHECKING:
    from src.main import PodcastApp

logger = logging.getLogger(__name__)


def get_current_user_or_none(request: Request) -> User | None:
    """Return typed current user from request state, if available."""
    current_user = getattr(request.state, "current_user", None)
    if isinstance(current_user, User):
        return current_user
    return None


def get_current_user(request: Request) -> User:
    """Return typed current user from request state or fail fast."""
    current_user = get_current_user_or_none(request)
    if current_user is None:
        raise RuntimeError("Current user is not available in request state")
    return current_user


async def attach_current_user(request: Request) -> None:
    """Load `request.state.current_user` from `UserSession.public_id` cookie."""
    request.state.current_user = None
    podcast_app: "PodcastApp" = request.app  # type: ignore[assignment]

    try:
        settings = podcast_app.settings
    except AttributeError:
        logger.exception("Auth: Failed to resolve settings from current app")
        return

    public_id = request.cookies.get(settings.auth.session_cookie_name)
    if not public_id:
        logger.info("Auth: unable to resolve public ID from session cookie")
        return

    try:
        async with SASessionUOW() as uow:
            repo = UserSessionRepository(session=uow.session)
            pair = await repo.get_active_with_user(public_id)
            if pair:
                _, user = pair
                request.state.current_user = user
    except Exception as exc:
        logger.exception("Auth: failed to resolve session user from cookie: %r", exc)
