from typing import Self

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped


class BaseModel(AsyncAttrs, DeclarativeBase):
    id: Mapped[int]

    def to_dict(self, excluded_fields: list[str] | None = None) -> dict:
        excluded_fields = excluded_fields or []
        res = {}
        for field in self.__dict__:
            if field not in excluded_fields and not field.startswith("_"):
                res[field] = getattr(self, field)

        return res

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        instance = cls()
        for key, value in data.items():
            setattr(instance, key, value)

        return instance
