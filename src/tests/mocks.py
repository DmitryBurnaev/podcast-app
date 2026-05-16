from unittest.mock import AsyncMock, Mock


class MockSession:
    def __init__(self) -> None:
        self.commit = AsyncMock(return_value=None)
        self.flush = AsyncMock(return_value=None)
        self.rollback = AsyncMock(return_value=None)


class MockUOW:
    def __init__(self, session: MockSession | None = None) -> None:
        self.session = session or MockSession()
        self.mark_for_commit = Mock(return_value=None)

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
