from unittest.mock import AsyncMock, Mock


class MockSession:
    def __init__(self) -> None:
        self.flush = AsyncMock(return_value=None)


class MockUOW:
    def __init__(self, session: MockSession | None = None) -> None:
        self.session = session or MockSession()
        self.mark_for_commit = Mock(return_value=None)

    async def __aenter__(self) -> "MockUOW":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None
