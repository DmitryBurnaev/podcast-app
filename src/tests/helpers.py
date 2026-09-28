from contextlib import asynccontextmanager
from typing import Any

from httpx import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from modules.schemas.errors import ErrorCode

DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_PARAMETERS: "Requested data is not valid.",
    ErrorCode.AUTH_INVALID: "Authentication credentials are invalid.",
}


def assert_error_response(
    response: Response,
    *,
    status_code: int,
    code: str,
    message: str | None = None,
    details: str | dict[str, str] | list[dict] | None = None,
) -> dict[str, Any]:
    """Assert the common API error envelope and return the error payload."""
    assert response.status_code == status_code, response.text

    response_data = response.json()
    assert "error" in response_data, response_data

    error = response_data["error"]
    assert error["code"] == code, error

    message = message or DEFAULT_MESSAGES.get(ErrorCode(code))
    if message is not None:
        assert error["message"] == message, error

    if details is not None:
        assert error["details"] == details, error

    return error


@asynccontextmanager
async def make_db_session():
    session_factory = async_sessionmaker(class_=AsyncSession)
    async_session = session_factory()
    await async_session.__aenter__()
    yield async_session
    await async_session.__aexit__(None, None, None)
