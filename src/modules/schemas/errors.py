from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCode(StrEnum):
    """Stable API error codes exposed to clients."""

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
    """Structured API error payload."""

    code: ErrorCode
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    """Structured API error response."""

    error: ErrorPayload
