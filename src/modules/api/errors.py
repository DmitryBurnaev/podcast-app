import logging
from enum import StrEnum
from typing import Any, cast

from litestar.connection import Request
from litestar.exceptions import HTTPException, ValidationException
from litestar.response import Response
from pydantic import BaseModel

from src.exceptions import BaseApplicationError

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    AUTH_MISSING = "AUTH_MISSING"
    AUTH_INVALID = "AUTH_INVALID"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REFRESH_EXPIRED = "REFRESH_EXPIRED"
    SESSION_INACTIVE = "SESSION_INACTIVE"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorPayload(BaseModel):
    code: ErrorCode
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorPayload


class APIError(BaseApplicationError):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message = "Something went wrong."

    def __init__(
        self,
        code: ErrorCode | str | None = None,
        message: str | None = None,
        details: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(details=details, message=message, status_code=status_code)
        if code is not None:
            self.code = ErrorCode(code)


class AuthMissingError(APIError):
    code = ErrorCode.AUTH_MISSING
    message = "Authentication credentials were not provided."
    status_code = 401


class AuthInvalidError(APIError):
    code = ErrorCode.AUTH_INVALID
    message = "Authentication credentials are invalid."
    status_code = 401


class TokenExpiredError(APIError):
    code = ErrorCode.TOKEN_EXPIRED
    message = "Access token expired."
    status_code = 401


class RefreshExpiredError(APIError):
    code = ErrorCode.REFRESH_EXPIRED
    message = "Refresh token expired."
    status_code = 401


class SessionInactiveError(APIError):
    code = ErrorCode.SESSION_INACTIVE
    message = "Session is inactive or expired."
    status_code = 401


class ForbiddenError(APIError):
    code = ErrorCode.FORBIDDEN
    message = "You do not have permission to perform this action."
    status_code = 403


class InvalidParametersError(APIError):
    code = ErrorCode.INVALID_PARAMETERS
    message = "Requested data is not valid."
    status_code = 400


class NotFoundAPIError(APIError):
    code = ErrorCode.NOT_FOUND
    message = "Requested object was not found."
    status_code = 404


class ConflictError(APIError):
    code = ErrorCode.CONFLICT
    message = "Requested operation conflicts with the current state."
    status_code = 409


def api_error_response(
    code: ErrorCode | str,
    message: str,
    *,
    details: Any = None,
    status_code: int = 500,
) -> Response[dict[str, Any]]:
    payload = ErrorResponse(
        error=ErrorPayload(code=ErrorCode(code), message=message, details=details)
    )
    return Response(
        content=payload.model_dump(mode="json"),
        media_type="application/json",
        status_code=status_code,
    )


def api_error_handler(_: Request, exc: APIError) -> Response[dict[str, Any]]:
    status_code = cast(int, exc.status_code)
    log_level = logging.ERROR if status_code >= 500 else logging.WARNING
    logger.log(log_level, "API error: %s", exc)
    return api_error_response(
        code=exc.code,
        message=exc.message,
        details=exc.details,
        status_code=status_code,
    )


def app_error_handler(_: Request, exc: BaseApplicationError) -> Response[dict[str, Any]]:
    status_code = cast(int, exc.status_code)
    code = ErrorCode.INTERNAL_ERROR
    if status_code == 400:
        code = ErrorCode.INVALID_PARAMETERS
    elif status_code == 401:
        code = ErrorCode.AUTH_INVALID
    elif status_code == 403:
        code = ErrorCode.FORBIDDEN
    elif status_code == 404:
        code = ErrorCode.NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.CONFLICT

    logger.log(exc.log_level, "Application error: %s", exc)
    return api_error_response(
        code=code,
        message=exc.message,
        details=exc.details,
        status_code=status_code,
    )


def validation_error_handler(_: Request, exc: ValidationException) -> Response[dict[str, Any]]:
    return api_error_response(
        code=ErrorCode.INVALID_PARAMETERS,
        message="Requested data is not valid.",
        details=getattr(exc, "extra", None) or getattr(exc, "detail", None),
        status_code=400,
    )


def http_error_handler(_: Request, exc: HTTPException) -> Response[dict[str, Any]]:
    status_code = cast(int, getattr(exc, "status_code", 500) or 500)
    code = ErrorCode.INTERNAL_ERROR
    if status_code == 400:
        code = ErrorCode.INVALID_PARAMETERS
    elif status_code == 401:
        code = ErrorCode.AUTH_INVALID
    elif status_code == 403:
        code = ErrorCode.FORBIDDEN
    elif status_code == 404:
        code = ErrorCode.NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.CONFLICT

    return api_error_response(
        code=code,
        message=str(getattr(exc, "detail", None) or "HTTP error."),
        status_code=status_code,
    )
