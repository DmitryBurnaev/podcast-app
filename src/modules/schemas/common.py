from typing import Generic, TypeVar, Literal

from pydantic import BaseModel, Field

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class LimitOffsetPagination(BaseModel, Generic[ResponseModelT]):
    """Limit and offset pagination for API responses."""

    offset: int = Field(default=0, description="Offset of the first item to return")
    items: list[ResponseModelT] = Field(
        default_factory=list, description="List of items for requested limit and offset"
    )
    total: int = Field(default=0, description="Total number of items in the database")


class OKResponse(BaseModel):
    """API response with successful answer."""

    status: Literal["ok"] = "ok"
