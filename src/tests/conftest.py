from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient
from pydantic import SecretStr

from src.main import PodcastApp, make_app
from src.settings.app import AppSettings, FlagsSettings
from src.settings.log import LogSettings


def _make_settings(*, api_debug_mode: bool) -> AppSettings:
    return AppSettings(
        app_secret_key=SecretStr("test-secret-key"),
        app_version="test",
        flags=FlagsSettings(debug_mode=True, api_debug_mode=api_debug_mode),
        log=LogSettings(format="[%(levelname)s] %(message)s"),
        api_docs_enabled=False,
    )


@pytest.fixture
def app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    settings = _make_settings(api_debug_mode=True)
    monkeypatch.setattr("src.main.get_app_settings", lambda: settings)
    return settings


@pytest.fixture
def auth_required_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    settings = _make_settings(api_debug_mode=False)
    monkeypatch.setattr("src.main.get_app_settings", lambda: settings)
    return settings


@pytest.fixture(autouse=True)
def mocked_redis_health(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mocked_check = AsyncMock(return_value=None)
    monkeypatch.setattr("src.modules.api.misc.check_redis_connection", mocked_check)
    return mocked_check


@pytest.fixture
def app(app_settings: AppSettings) -> PodcastApp:
    return make_app(settings=app_settings)


@pytest.fixture
def auth_required_app(auth_required_settings: AppSettings) -> PodcastApp:
    return make_app(settings=auth_required_settings)


@pytest.fixture
def client(app: PodcastApp) -> Generator[TestClient[PodcastApp], None, None]:
    yield TestClient(app=app, raise_server_exceptions=False)


@pytest.fixture
def auth_required_client(
    auth_required_app: PodcastApp,
) -> Generator[TestClient[PodcastApp], None, None]:
    yield TestClient(app=auth_required_app, raise_server_exceptions=False)
