from abc import ABC
from collections.abc import Callable, Iterator
from typing import Any, ClassVar, TypeVar
from unittest import mock
from unittest.mock import AsyncMock, Mock

import pytest


class BaseMock:
    """Stateful class replacement installed for the lifetime of a pytest fixture."""

    target_class: ClassVar[type[Any]]
    mocked_methods: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:
        self.target_obj: Any | None = None
        self.init_mock: Mock | None = None

    def mock_init(self, target_obj: Any, *args: Any, **kwargs: Any) -> None:
        """Record the latest production object created through the patched class."""
        self.target_obj = target_obj

    def get_mocks(self) -> dict[str, Callable[..., Any]]:
        """Return explicitly declared methods that should replace target methods."""
        return {name: getattr(self, name) for name in self.mocked_methods}


class BaseMockWithContextManager(BaseMock, ABC):
    """Base replacement for async context-managed clients."""

    mocked_methods = ("__aenter__", "__aexit__")

    def __init__(self) -> None:
        super().__init__()
        self.enter_mock = AsyncMock(return_value=self)
        self.exit_mock = AsyncMock(side_effect=self._process_exit)

    async def __aenter__(self, *_: Any) -> "BaseMockWithContextManager":
        """Enter through the fake even when invoked as a patched special method."""
        return await self.enter_mock()

    async def __aexit__(self, *args: Any) -> None:
        """Forward the exception triple without the patched target instance."""
        exc_type, exc_val, exc_tb = args[-3:]
        await self.exit_mock(exc_type, exc_val, exc_tb)

    @staticmethod
    async def _process_exit(
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if exc_val is not None:
            raise exc_val


BaseMockT = TypeVar("BaseMockT", bound=BaseMock)


def mock_target_class(
    fake: BaseMockT,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[BaseMockT]:
    """Patch a concrete class constructor and selected methods with one stateful fake."""

    target_class = fake.target_class

    def init_method(target_obj: Any, *args: Any, **kwargs: Any) -> None:
        fake.mock_init(target_obj, *args, **kwargs)

    init_mock = mock.create_autospec(target_class.__init__, return_value=None)

    def patched_init(target_obj: Any, *args: Any, **kwargs: Any) -> None:
        init_mock(target_obj, *args, **kwargs)
        init_method(target_obj, *args, **kwargs)

    fake.init_mock = init_mock
    monkeypatch.setattr(target_class, "__init__", patched_init)
    for name, replacement in fake.get_mocks().items():
        monkeypatch.setattr(target_class, name, replacement)
    yield fake


class MockSession:
    def __init__(self) -> None:
        self.commit = AsyncMock(return_value=None)
        self.flush = AsyncMock(return_value=None)
        self.rollback = AsyncMock(return_value=None)


class MockUOW:
    def __init__(self, session: MockSession | None = None) -> None:
        self.session = session or MockSession()
        self.mark_for_commit = Mock(return_value=None)
        self.flush = self.session.flush
        self.commit = self.session.commit
        self.rollback = self.session.rollback

    async def __aenter__(self) -> "MockUOW":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class MockRedisClient:
    def __init__(self, content: dict | None = None) -> None:
        self.content = content or {}
        self.sync_redis = object()
        self.get = Mock(side_effect=lambda key: self.content.get(key))
        self.set = Mock(return_value=None)
        self.publish = Mock(return_value=None)
        self.async_get = AsyncMock(side_effect=lambda key: self.content.get(key))
        self.async_get_many = AsyncMock(return_value=self.content)
        self.async_publish = AsyncMock(return_value=None)
        self.async_set = AsyncMock(return_value=None)
        self.async_pubsub = Mock(return_value=object())

    @staticmethod
    def get_key_by_filename(filename: str) -> str:
        return filename.partition(".")[0]


class MockStorageS3:
    def __init__(self) -> None:
        self.copy_file = AsyncMock(return_value="remote/copied.mp3")
        self.delete_file = AsyncMock(return_value={})
        self.download_file = AsyncMock(return_value="/tmp/downloaded.mp3")
        self.get_file_info = AsyncMock(return_value=None)
        self.get_file_size = AsyncMock(return_value=0)
        self.get_presigned_url = AsyncMock(return_value="https://storage/presigned")
        self.upload_file = AsyncMock(return_value="remote/uploaded.mp3")
