import logging
import secrets
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.modules.auth.hashers import PBKDF2PasswordHasher
from src.modules.db.models import BaseModel
from src.utils import utcnow

# from common.utils import utcnow
# from common.models import ModelMixin
# from core.database import ModelBase
# from src.modules.auth.hasher import PBKDF2PasswordHasher
# from src.modules.auth.constants import LENGTH_USER_ACCESS_TOKEN

logger = logging.getLogger(__name__)

# Nbytes default for secrets.token_urlsafe; stored token truncated to this length.
LENGTH_USER_ACCESS_TOKEN = 32


class User(BaseModel):
    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(128), nullable=True)
    password: Mapped[str] = mapped_column(sa.String(128))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    is_superuser: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())

    def __str__(self) -> str:
        return self.display_name

    def __repr__(self):
        return f"<User #{self.id} {self.email}>"

    @classmethod
    def make_password(cls, raw_password: str) -> str:
        hasher = PBKDF2PasswordHasher()
        return hasher.encode(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        hasher = PBKDF2PasswordHasher()
        verified, _ = hasher.verify(raw_password, encoded=str(self.password))
        return verified

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.email

    # @classmethod
    # async def get_active(cls, db_session: AsyncSession, user_id: int) -> "User":
    #     return await cls.async_get(db_session, id=user_id, is_active=True)
    #


class UserInvite(BaseModel):
    __tablename__ = "auth_invites"
    TOKEN_MAX_LENGTH = 32

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("auth_users.id"),
        unique=True,
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(
        sa.String(length=128),
        unique=True,
        nullable=True,
    )
    token: Mapped[str] = mapped_column(
        sa.String(length=32),
        unique=True,
        nullable=False,
        index=True,
    )
    is_applied: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.false(),
        default=False,
    )
    expired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("auth_users.id"), nullable=False)

    @classmethod
    def generate_token(cls) -> str:
        return secrets.token_urlsafe()[: cls.TOKEN_MAX_LENGTH]

    def __repr__(self) -> str:
        return f"<UserInvite #{self.id} {self.email}>"


class UserSession(BaseModel):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        sa.String(length=36),
        index=True,
        nullable=False,
        unique=True,
    )
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("auth_users.id"), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(sa.String(length=512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.true(),
        default=True,
    )
    expired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<UserSession #{self.id} {self.user_id}>"


class UserIP(BaseModel):
    __tablename__ = "auth_user_ips"
    __table_args__ = (
        sa.Index(
            "ix_auth_user_ips__user_id__hashed_address",
            "user_id",
            "hashed_address",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hashed_address: Mapped[str] = mapped_column(sa.String(length=128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("auth_users.id"), nullable=True)
    registered_by: Mapped[str] = mapped_column(
        sa.String(length=128),
        index=True,
        nullable=False,
        server_default="",
        default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<UserIP {self.hashed_address} user: {self.user_id}>"


class UserAccessToken(BaseModel):
    __tablename__ = "auth_user_access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("auth_users.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(length=256), nullable=False)
    token: Mapped[str] = mapped_column(sa.String(length=256), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.true(),
        default=True,
    )
    expires_in: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    @classmethod
    def generate_token(cls, length: int = LENGTH_USER_ACCESS_TOKEN) -> str:
        return secrets.token_urlsafe(nbytes=length)[:LENGTH_USER_ACCESS_TOKEN]

    @property
    def active(self) -> bool:
        return self.enabled and self.expires_in >= utcnow()

    def __repr__(self) -> str:
        return f"<UserAccessToken {self.token} user: {self.user_id}>"
