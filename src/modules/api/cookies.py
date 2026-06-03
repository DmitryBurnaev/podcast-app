from typing import Annotated

from litestar import delete, get, post, put
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.constants import SourceType
from src.modules.api.base import BaseApiController
from exceptions import InvalidParametersAPIError, NotFoundAPIAPIError, StateConflictAPIError
from src.modules.db import User
from src.modules.db.models.podcasts import Cookie
from src.modules.db.repositories import CookieRepository, EpisodeRepository
from src.modules.db.services import SASessionUOW
from src.modules.schemas.cookies import CookieResponse
from src.utils import utcnow


class CookieAPIController(BaseApiController):
    path = "/api/cookies"
    tags = ["Cookies"]

    @get("/")
    async def get_list(self, current_user: User) -> list[CookieResponse]:
        """Return the latest cookie for each source type owned by the current user."""
        async with SASessionUOW() as uow:
            cookies = await CookieRepository(uow.session).all(owner_id=current_user.id)

        latest_by_source: dict[SourceType, Cookie] = {}
        for cookie in sorted(cookies, key=lambda item: item.created_at, reverse=True):
            latest_by_source.setdefault(cookie.source_type, cookie)

        return [
            CookieResponse.model_validate(cookie)
            for cookie in sorted(latest_by_source.values(), key=lambda item: item.source_type)
        ]

    @post("/", status_code=HTTP_201_CREATED)
    async def create(
        self,
        current_user: User,
        data: Annotated[dict[str, object], Body(media_type=RequestEncodingType.MULTI_PART)],
    ) -> CookieResponse:
        """Create an encrypted cookie file record."""
        source_type, encrypted_data = await self._parse_cookie_form(data)
        async with SASessionUOW() as uow:
            cookie = await CookieRepository(uow.session).create(
                source_type=source_type,
                data=encrypted_data,
                owner_id=current_user.id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            await uow.session.flush()

        return CookieResponse.model_validate(cookie)

    @get("/{cookie_id:int}/")
    async def get_details(self, cookie_id: int, current_user: User) -> CookieResponse:
        """Return details for a cookie owned by the current user."""
        async with SASessionUOW() as uow:
            cookie = await CookieRepository(uow.session).first(
                id=cookie_id,
                owner_id=current_user.id,
            )
        if cookie is None:
            raise NotFoundAPIAPIError()

        return CookieResponse.model_validate(cookie)

    @put("/{cookie_id:int}/")
    async def update(
        self,
        cookie_id: int,
        current_user: User,
        data: Annotated[dict[str, object], Body(media_type=RequestEncodingType.MULTI_PART)],
    ) -> CookieResponse:
        """Replace an encrypted cookie file record."""
        source_type, encrypted_data = await self._parse_cookie_form(data)
        async with SASessionUOW() as uow:
            cookie_repository = CookieRepository(uow.session)
            cookie = await cookie_repository.first(id=cookie_id, owner_id=current_user.id)
            if cookie is None:
                raise NotFoundAPIAPIError()

            await cookie_repository.update(
                cookie,
                source_type=source_type,
                data=encrypted_data,
                updated_at=utcnow(),
            )

        return CookieResponse.model_validate(cookie)

    @delete("/{cookie_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(self, cookie_id: int, current_user: User) -> None:
        """Delete a cookie owned by the current user."""
        async with SASessionUOW() as uow:
            cookie_repository = CookieRepository(uow.session)
            cookie = await cookie_repository.first(id=cookie_id, owner_id=current_user.id)
            if cookie is None:
                raise NotFoundAPIAPIError()

            linked_episodes = await EpisodeRepository(uow.session).get_total_count(
                cookie_id=cookie_id,
                owner_id=current_user.id,
            )
            if linked_episodes:
                raise StateConflictAPIError(message="There are episodes related to this cookie.")

            await cookie_repository.delete(cookie)

    @classmethod
    async def _parse_cookie_form(cls, data: dict[str, object]) -> tuple[SourceType, str]:
        source_type_raw = data.get("source_type")
        if not source_type_raw:
            raise InvalidParametersAPIError(details={"source_type": "Source type is required."})

        try:
            source_type = SourceType(str(source_type_raw).upper())
        except ValueError as exc:
            raise InvalidParametersAPIError(
                details={"source_type": "Unsupported source type."}
            ) from exc

        uploaded_file = data.get("file") or next(
            (value for value in data.values() if isinstance(value, UploadFile)),
            None,
        )
        if not isinstance(uploaded_file, UploadFile):
            raise InvalidParametersAPIError(details={"file": "Cookie file is required."})

        try:
            file_content = (await uploaded_file.read()).decode()
        except UnicodeDecodeError as exc:
            raise InvalidParametersAPIError(details={"file": str(exc)}) from exc

        return source_type, Cookie.get_encrypted_data(file_content)
