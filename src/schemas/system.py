from datetime import datetime

from pydantic import BaseModel, Field


class SystemInfo(BaseModel):
    """System information response model."""

    status: str = "ok"
    vendors: list[str] = Field(default_factory=list)


class HealthCheck(BaseModel):
    """Health check response model."""

    status: str
    timestamp: datetime
