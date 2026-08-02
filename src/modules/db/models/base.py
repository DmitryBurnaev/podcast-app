import re
from typing import Self
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.utils import utcnow


class BaseModel(AsyncAttrs, DeclarativeBase):
    id: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        server_default=sa.text("now()"),
        nullable=False,
    )

    def __str__(self) -> str:
        return f"Instance of {self.__class__.__name__} #{self.id}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id} created_at={self.created_at}>"

    def to_dict(self, excluded_fields: list[str] | None = None) -> dict:
        """Return a plain dictionary of public mapped attributes."""
        excluded_fields = excluded_fields or []
        res = {}
        for field in self.__dict__:
            if field not in excluded_fields and not field.startswith("_"):
                res[field] = getattr(self, field)

        return res

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create an instance and populate attributes from a dictionary."""
        instance = cls()
        for key, value in data.items():
            setattr(instance, key, value)

        return instance

    @property
    def admin_url_name(self) -> str:
        """Return the admin url name. Split name by rule: CamelCase -> kebab-case"""
        name = self.__class__.__name__
        name = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
        return name
