from typing import Any

from httpx import Response


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
        assert error["message"] == message, error

    return error
