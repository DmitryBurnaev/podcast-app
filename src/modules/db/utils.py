import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import SourceType
from src.modules.db.models.podcasts import Cookie
from src.modules.db.repositories import CookieRepository
from src.modules.utils import processing as processing_utils

logger = logging.getLogger(__name__)


@asynccontextmanager
async def cookie_file_ctx(
    db_session: AsyncSession,
    user_id: int | None = None,
    source_type: SourceType | None = None,
    cookie_id: int | None = None,
) -> AsyncGenerator[Cookie | None, Any]:
    """
    Async context which allows to save tmp file (with decrypted cookie's data)
    and remove it after using (by sec reason)

    :param db_session: current SA's async session
    :param user_id: current logged-in user (needed for searching cookie)
    :param source_type: required source (needed for searching cookie)
    :param cookie_id: if known - will be used for direct access

    """
    logger.debug(
        "Entering cookie's file context: user %s | source_type: %s | cookie_id: %s",
        user_id,
        source_type,
        cookie_id,
    )
    cookie_repository = CookieRepository(db_session)
    cookie: Cookie | None = None
    if cookie_id:
        cookie = await cookie_repository.get(cookie_id)
    elif user_id and source_type:
        cookie_filter: dict[str, str | int] = {"source_type": source_type, "owner_id": user_id}
        cookies = await cookie_repository.all(**cookie_filter)
        if cookies:
            cookie = cookies[0]
    else:
        cookie = None

    try:
        if cookie:
            cookie.file_path = await cookie.as_file()
            yield cookie
        else:
            yield None
    finally:
        if cookie:
            processing_utils.delete_file(cookie.file_path)
