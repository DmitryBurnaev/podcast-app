"""Resolve browser session cookie to ORM User on each HTTP request."""

import logging

from litestar.connection import Request

from src.modules.db.repositories import UserSessionRepository
from src.modules.db.services import SASessionUOW

logger = logging.getLogger(__name__)


async def attach_current_user(request: Request) -> None:
    """Load `request.state.current_user` from `UserSession.public_id` cookie."""
    request.state.current_user = None
    try:
        settings = request.app.settings
    except AttributeError:
        return
    public_id = request.cookies.get(settings.auth.session_cookie_name)
    if not public_id:
        return
    try:
        async with SASessionUOW() as uow:
            repo = UserSessionRepository(session=uow.session)
            pair = await repo.get_active_with_user(public_id)
            if pair:
                _, user = pair
                request.state.current_user = user
    except Exception:
        logger.exception("Failed to resolve session user from cookie")
