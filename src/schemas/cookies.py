from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CookieResponse(BaseModel):
    """API response with uploaded cookie metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    created_at: datetime
