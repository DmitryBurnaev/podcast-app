from contextlib import asynccontextmanager
from typing import Any

from httpx import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


def assert_error_response(
    response: Response,
    *,
    status_code: int,
    code: str,
    message: str | None = None,
) -> dict[str, Any]:
    """Assert the common API error envelope and return the error payload."""
    assert response.status_code == status_code, response.text

    response_data = response.json()
    assert "error" in response_data, response_data

    error = response_data["error"]
    assert error["code"] == code, error
    if message is not None:
        assert error["details"] == message, error

    return error


@asynccontextmanager
async def make_db_session():
    session_factory = async_sessionmaker(class_=AsyncSession)
    async_session = session_factory()
    await async_session.__aenter__()
    yield async_session
    await async_session.__aexit__(None, None, None)
