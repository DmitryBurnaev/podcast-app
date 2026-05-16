import pytest
from litestar.exceptions import HTTPException

from src.exceptions import BaseApplicationError
from src.modules.api.errors import APIError, app_error_handler, http_error_handler


def test_api_error__accepts_string_code() -> None:
    error = APIError(code="NOT_FOUND")

    assert error.code == "NOT_FOUND"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "INVALID_PARAMETERS"),
        (401, "AUTH_INVALID"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (409, "CONFLICT"),
        (500, "INTERNAL_ERROR"),
    ],
)
def test_app_error_handler__maps_status_codes(status_code: int, expected_code: str) -> None:
    response = app_error_handler(
        None,
        BaseApplicationError(
            message="Application failed",
            details={"field": "value"},
            status_code=status_code,
        ),
    )

    assert response.status_code == status_code
    assert response.content["error"] == {
        "code": expected_code,
        "message": "Application failed",
        "details": {"field": "value"},
    }


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "AUTH_INVALID"),
        (403, "FORBIDDEN"),
    ],
)
def test_http_error_handler__maps_auth_status_codes(
    status_code: int,
    expected_code: str,
) -> None:
    response = http_error_handler(None, HTTPException(status_code=status_code, detail="Denied"))

    assert response.status_code == status_code
    assert response.content["error"]["code"] == expected_code
    assert response.content["error"]["message"] == "Denied"
