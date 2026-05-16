from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.datastructures import UploadFile
from litestar.testing import TestClient

from src.constants import SourceType
from src.main import PodcastApp
from src.modules.api.cookies import CookieAPIController
from src.modules.api.errors import InvalidParametersError
from src.modules.db.models import User
from src.tests.factories import make_cookie
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW
from src.utils import utcnow


@pytest.fixture
def cookie_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(
        all=AsyncMock(),
        create=AsyncMock(),
        delete=AsyncMock(),
        first=AsyncMock(),
        update=AsyncMock(),
    )
    monkeypatch.setattr("src.modules.api.cookies.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr("src.modules.api.cookies.CookieRepository", lambda session: repository)
    return repository


@pytest.fixture
def cookie_episode_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(get_total_count=AsyncMock(return_value=0))
    monkeypatch.setattr("src.modules.api.cookies.EpisodeRepository", lambda session: repository)
    return repository


class TestCookieListAPI:
    url = "/api/cookies/"

    def test_get_list__returns_latest_per_source(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
    ) -> None:
        now = utcnow()
        old_youtube = make_cookie(
            id=1,
            owner_id=current_user.id,
            source_type=SourceType.YOUTUBE,
            created_at=now - timedelta(days=1),
        )
        new_youtube = make_cookie(
            id=2,
            owner_id=current_user.id,
            source_type=SourceType.YOUTUBE,
            created_at=now,
        )
        yandex = make_cookie(
            id=3,
            owner_id=current_user.id,
            source_type=SourceType.YANDEX,
            created_at=now,
        )
        cookie_repository.all.return_value = [old_youtube, yandex, new_youtube]

        response = client.get(self.url)

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert {item["id"] for item in response_data} == {new_youtube.id, yandex.id}
        cookie_repository.all.assert_awaited_once_with(owner_id=current_user.id)


class TestCookieCreateAPI:
    url = "/api/cookies/"

    def test_create__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cookie = make_cookie(id=10, owner_id=current_user.id)
        cookie_repository.create.return_value = cookie
        monkeypatch.setattr(
            "src.modules.api.cookies.Cookie.get_encrypted_data",
            lambda data: f"encrypted:{data}",
        )

        response = client.post(
            self.url,
            data={"source_type": "youtube"},
            files={"file": ("cookies.txt", b"raw-cookie", "text/plain")},
        )

        assert response.status_code == 201, response.text
        assert response.json()["id"] == cookie.id
        create_kwargs = cookie_repository.create.await_args.kwargs
        assert create_kwargs["source_type"] == SourceType.YOUTUBE
        assert create_kwargs["data"] == "encrypted:raw-cookie"
        assert create_kwargs["owner_id"] == current_user.id

    @pytest.mark.parametrize(
        ("data", "files", "details"),
        [
            ({}, {}, {"source_type": "Source type is required."}),
            (
                {"source_type": "unsupported"},
                {"file": ("cookies.txt", b"raw-cookie", "text/plain")},
                {"source_type": "Unsupported source type."},
            ),
        ],
    )
    def test_create__invalid_form__fail(
        self,
        client: TestClient[PodcastApp],
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        details: dict[str, str],
    ) -> None:
        response = client.post(self.url, data=data, files=files)

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == details

    async def test_create__missing_file__fail(self) -> None:
        with pytest.raises(InvalidParametersError) as exc_info:
            await CookieAPIController._parse_cookie_form({"source_type": "youtube"})

        assert getattr(exc_info.value, "details", None) == {"file": "Cookie file is required."}

    async def test_create__undecodable_file__fail(self) -> None:
        uploaded_file = UploadFile(
            content_type="text/plain",
            filename="cookies.txt",
            file_data=b"\xff",
        )

        with pytest.raises(InvalidParametersError) as exc_info:
            await CookieAPIController._parse_cookie_form(
                {"source_type": "youtube", "file": uploaded_file}
            )

        assert "codec can't decode byte" in getattr(exc_info.value, "details", {}).get("file", "")


class TestCookieDetailsAPI:
    url = "/api/cookies/{cookie_id}/"

    def test_get_details__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
    ) -> None:
        cookie = make_cookie(id=11, owner_id=current_user.id)
        cookie_repository.first.return_value = cookie

        response = client.get(self.url.format(cookie_id=cookie.id))

        assert response.status_code == 200, response.text
        assert response.json()["id"] == cookie.id
        cookie_repository.first.assert_awaited_once_with(id=cookie.id, owner_id=current_user.id)

    def test_get_details__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
    ) -> None:
        cookie_repository.first.return_value = None

        response = client.get(self.url.format(cookie_id=404))

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Requested object was not found.",
        )
        cookie_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    def test_update__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cookie = make_cookie(id=12, owner_id=current_user.id)
        cookie_repository.first.return_value = cookie
        monkeypatch.setattr(
            "src.modules.api.cookies.Cookie.get_encrypted_data",
            lambda data: f"encrypted:{data}",
        )

        response = client.put(
            self.url.format(cookie_id=cookie.id),
            data={"source_type": "yandex"},
            files={"file": ("cookies.txt", b"new-cookie", "text/plain")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["id"] == cookie.id
        cookie_repository.update.assert_awaited_once()
        update_kwargs = cookie_repository.update.await_args.kwargs
        assert update_kwargs["source_type"] == SourceType.YANDEX
        assert update_kwargs["data"] == "encrypted:new-cookie"

    def test_update__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cookie_repository.first.return_value = None
        monkeypatch.setattr(
            "src.modules.api.cookies.Cookie.get_encrypted_data",
            lambda data: f"encrypted:{data}",
        )

        response = client.put(
            self.url.format(cookie_id=404),
            data={"source_type": "youtube"},
            files={"file": ("cookies.txt", b"raw-cookie", "text/plain")},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Requested object was not found.",
        )
        cookie_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    def test_delete__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
        cookie_episode_repository: SimpleNamespace,
    ) -> None:
        cookie = make_cookie(id=13, owner_id=current_user.id)
        cookie_repository.first.return_value = cookie

        response = client.delete(self.url.format(cookie_id=cookie.id))

        assert response.status_code == 204, response.text
        cookie_episode_repository.get_total_count.assert_awaited_once_with(
            cookie_id=cookie.id,
            owner_id=current_user.id,
        )
        cookie_repository.delete.assert_awaited_once_with(cookie)

    def test_delete__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
    ) -> None:
        cookie_repository.first.return_value = None

        response = client.delete(self.url.format(cookie_id=404))

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Requested object was not found.",
        )
        cookie_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    def test_delete__linked_episodes__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        cookie_repository: SimpleNamespace,
        cookie_episode_repository: SimpleNamespace,
    ) -> None:
        cookie = make_cookie(id=11, owner_id=current_user.id)
        cookie_repository.first.return_value = cookie
        cookie_episode_repository.get_total_count.return_value = 2

        response = client.delete(self.url.format(cookie_id=cookie.id))

        assert_error_response(
            response,
            status_code=409,
            code="CONFLICT",
            message="There are episodes related to this cookie.",
        )
        cookie_repository.delete.assert_not_awaited()
