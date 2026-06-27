import logging
from typing import Any, cast

from litestar.connection import Request
from litestar.exceptions import HTTPException, ValidationException
from litestar.response import Response

from src.exceptions import APIError, BaseApplicationError
from src.modules.schemas.errors import ErrorCode, ErrorPayload, ErrorResponse

logger = logging.getLogger(__name__)


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
