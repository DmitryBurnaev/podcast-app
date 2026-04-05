"""Map secret token URLs (File.url) to S3 presigned redirects; no media bytes through Litestar."""

import logging

from litestar import get
from litestar.exceptions import NotFoundException
from litestar.response import Redirect
from litestar.status_codes import HTTP_307_TEMPORARY_REDIRECT

from src.exceptions import NotSupportedError
from src.modules.db import SASessionUOW
from src.modules.db.models.media import MediaType
from src.modules.db.repositories import FileRepository
from src.modules.views.base import BaseController

logger = logging.getLogger(__name__)


class MediaByTokenController(BaseController):
    """
    Resolve /m/ and /r/ token links to presigned S3 GET URLs.
    Covers stay on dedicated episode/podcast cover routes with local cache.
    """

    @get("/m/{access_token:str}/")
    async def get_private_media(self, access_token: str) -> Redirect:
        """Audio and image tokens: redirect to S3 (Range/CORS handled by storage)."""
        return await self._redirect_presigned(
            access_token,
            allowed_types=(MediaType.AUDIO, MediaType.IMAGE),
        )

    @get("/r/{access_token:str}/")
    async def get_rss_media(self, access_token: str) -> Redirect:
        """RSS file tokens: redirect to S3."""
        return await self._redirect_presigned(
            access_token,
            allowed_types=(MediaType.RSS,),
        )

    async def _redirect_presigned(
        self,
        access_token: str,
        allowed_types: tuple[MediaType, ...],
    ) -> Redirect:
        if not access_token or len(access_token) > 128:
            raise NotFoundException("Media not found")

        async with SASessionUOW() as uow:
            repo = FileRepository(session=uow.session)
            media_file = await repo.first_by_access_token(access_token)
            if (
                media_file is None
                or not media_file.available
                or media_file.type not in allowed_types
            ):
                raise NotFoundException("Media not found")

        try:
            url = await media_file.fetch_presigned_url()
        except NotSupportedError as exc:
            logger.warning(
                "[media_token] presign failed file_id=%s type=%s: %s",
                media_file.id,
                media_file.type,
                exc,
            )
            raise NotFoundException("Media not found") from exc

        logger.info("[media_token] redirect file_id=%s type=%s", media_file.id, media_file.type)
        return Redirect(path=url, status_code=HTTP_307_TEMPORARY_REDIRECT)
