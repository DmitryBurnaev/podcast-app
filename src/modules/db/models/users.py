import logging
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
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


class User(BaseModel):
    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(128), nullable=True)
    password: Mapped[str] = mapped_column(sa.String(128))
    is_admin: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    is_superuser: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    def __str__(self) -> str:
        return self.email

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

    @classmethod
    async def get_active(cls, db_session: AsyncSession, user_id: int) -> "User":
        return await cls.async_get(db_session, id=user_id, is_active=True)


#
# class UserInvite(BaseModel):
#     __tablename__ = "auth_invites"
#     TOKEN_MAX_LENGTH = 32
#
#     id = Column(Integer, primary_key=True)
#     user_id = Column(ForeignKey("auth_users.id"), unique=True)
#     email = Column(String(length=128), unique=True)
#     token = Column(String(length=32), unique=True, nullable=False, index=True)
#     is_applied = Column(Boolean, default=False, nullable=False)
#     expired_at = Column(DateTime(timezone=True), nullable=False)
#     created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
#     owner_id = Column(ForeignKey("auth_users.id"), nullable=False)
#
#     def __repr__(self):
#         return f"<UserInvite #{self.id} {self.email}>"
#
#     @classmethod
#     def generate_token(cls):
#         return secrets.token_urlsafe()[: cls.TOKEN_MAX_LENGTH]
#
#
# class UserSession(ModelBase, ModelMixin):
#     __tablename__ = "auth_sessions"
#
#     id = Column(Integer, primary_key=True)
#     public_id = Column(String(length=36), index=True, nullable=False, unique=True)
#     user_id = Column(ForeignKey("auth_users.id"))
#     refresh_token = Column(String(length=512))
#     is_active = Column(Boolean, default=True, nullable=False)
#     expired_at = Column(DateTime(timezone=True), nullable=False)
#     created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
#     refreshed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
#
#     def __repr__(self):
#         return f"<UserSession #{self.id} {self.user_id}>"
#
#
# class UserIP(ModelBase, ModelMixin):
#     __tablename__ = "auth_user_ips"
#     __table_args__ = (
#         Index(
#             "ix_auth_user_ips__user_id__hashed_address",
#             "user_id",
#             "hashed_address",
#         ),
#     )
#
#     id = Column(Integer, primary_key=True)
#     hashed_address = Column(String(length=128), nullable=False)
#     user_id = Column(ForeignKey("auth_users.id"))
#     registered_by = Column(String(length=128), index=True, nullable=False, server_default="")
#     created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
#
#     class Meta:
#         order_by = ("-id",)
#
#     def __repr__(self):
#         return f"<UserIP {self.hashed_address} user: {self.user_id}>"
#
#
# class UserAccessToken(ModelBase, ModelMixin):
#     __tablename__ = "auth_user_access_tokens"
#
#     id = Column(Integer, primary_key=True)
#     user_id = Column(ForeignKey("auth_users.id"), nullable=False)
#     name = Column(String(length=256), nullable=False)
#     token = Column(String(length=256), unique=True, nullable=False)
#     enabled = Column(Boolean, default=True, nullable=False)
#     expires_in = Column(DateTime(timezone=True), nullable=False)
#     created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
#
#     class Meta:
#         order_by = ("-id",)
#
#     def __repr__(self):
#         return f"<UserAccessToken {self.token} user: {self.user_id}>"
#
#     @classmethod
#     def generate_token(cls, length: int = LENGTH_USER_ACCESS_TOKEN) -> str:
#         return secrets.token_urlsafe(nbytes=length)[:LENGTH_USER_ACCESS_TOKEN]
#
#     @property
#     def active(self) -> bool:
#         return self.enabled and self.expires_in >= utcnow()
