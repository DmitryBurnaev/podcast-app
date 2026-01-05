from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped


class BaseModel(AsyncAttrs, DeclarativeBase):
    id: Mapped[int]
