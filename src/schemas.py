from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

__all__ = (
    "SystemInfo",
    "HealthCheck",
)


class SystemInfo(BaseModel):
    """System information response model."""

    status: str = "ok"
    vendors: list[str] = Field(default_factory=list)


class HealthCheck(BaseModel):
    """Health check response model."""

    status: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Base error response model"""

    error: str
    detail: Optional[str] = None
