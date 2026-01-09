import logging
import os.path
import urllib.parse
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from src.exceptions import NotSupportedError
from src.modules.auth.hashers import get_random_hash
from src.modules.db.models import BaseModel
from src.settings.app import get_app_settings
from src.utils import utcnow

logger = logging.getLogger(__name__)
TOKEN_LENGTH = 48


class FileType(str, Enum):
    """File type enumeration"""

    AUDIO = "audio"
    RSS = "rss"
    IMAGE = "image"


class File(BaseModel):
    """SQLAlchemy schema for file instances"""

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    type: Mapped[FileType] = mapped_column(sa.Enum(FileType), nullable=False)
    path: Mapped[str] = mapped_column(sa.String(length=256), nullable=False, default="")
    size: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    source_url: Mapped[str] = mapped_column(sa.String(length=512), nullable=False, default="")
    available: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    access_token: Mapped[str] = mapped_column(
        sa.String(length=64), nullable=False, index=True, unique=True
    )
    owner_id: Mapped[int] = mapped_column(
        sa.ForeignKey("auth_users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    public: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=expression.false(), default=False
    )
    meta: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    hash: Mapped[str] = mapped_column(sa.String(length=32), nullable=False, server_default="")

    def __repr__(self):
        return f'<File #{self.id} | {self.type} | "{self.path}">'

    @classmethod
    def generate_token(cls) -> str:
        return get_random_hash(TOKEN_LENGTH)

    @classmethod
    def token_is_correct(cls, token: str) -> bool:
        return token.isalnum() and len(token) == TOKEN_LENGTH

    @property
    def url(self) -> str | None:
        """Returns file URL based on public/private status"""
        app_settings = get_app_settings()

        if self.public:
            if self.source_url:
                return self.source_url

            if app_settings.s3.storage_url:
                return urllib.parse.urljoin(
                    app_settings.s3.storage_url,
                    f"{app_settings.s3.bucket_name}/{self.path}",
                )

        if not self.available:
            return None

        pattern = {
            FileType.RSS: f"/r/{self.access_token}/",
            FileType.IMAGE: f"/m/{self.access_token}/",
            FileType.AUDIO: f"/m/{self.access_token}/",
        }
        return urllib.parse.urljoin(app_settings.service_url, pattern[self.type])

    @property
    async def presigned_url(self) -> str:
        """Returns presigned URL for S3 file access"""
        if self.available and not self.path:
            raise NotSupportedError(f"Remote file {self} available but has not remote path.")

        # TODO: implement StorageS3 integration
        raise NotImplementedError("Presigned URL generation not implemented")
        # url = await StorageS3().get_presigned_url(self.path)
        # logger.debug("Generated URL for %s: %s", self, url)
        # return url

    @property
    def content_type(self) -> str:
        return f"{self.type.lower()}/{self.name.split('.')[-1]}"

    @property
    def headers(self) -> dict:
        return {"content-length": str(self.size or 0), "content-type": self.content_type}

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    #
    # async def delete(
    #     self, db_session: AsyncSession, db_flush: bool = True, remote_path: str = None
    # ):
    #     filter_kwargs = {"path": self.path, "id__ne": self.id, "available__is": True}
    #     if same_files := (await File.async_filter(db_session, **filter_kwargs)).all():
    #         file_infos = [(file.id, file.type) for file in same_files]
    #         logger.warning(
    #             "There are another relations for the file %s: %s. Skip file removing.",
    #             self.path,
    #             file_infos,
    #         )
    #
    #     elif not self.available:
    #         logger.debug("Skip deleting not-available file: %s", self)
    #
    #     else:
    #         remote_path = remote_path or REMOTE_PATH_MAP[self.type]
    #         logger.debug("Removing file from S3: %s | called by: %s", remote_path, self)
    #         await StorageS3().delete_files_async([self.name], remote_path=remote_path)
    #
    #     return await super().delete(db_session, db_flush)

    # @classmethod
    # async def create(
    #     cls,
    #     db_session: AsyncSession,
    #     file_type: FileType,
    #     available: bool = True,
    #     **file_kwargs,
    # ) -> "File":
    #     file_kwargs |= {
    #         "available": available,
    #         "access_token": File.generate_token(),
    #         "type": file_type,
    #     }
    #     logger.debug("Creating new file: %s", file_kwargs)
    #     return await File.async_create(db_session=db_session, **file_kwargs)
    #
    # @classmethod
    # async def copy(
    #     cls, db_session: AsyncSession, file_id: int, owner_id: int, available: bool = True
    # ) -> "File":
    #     source_file: File = await File.async_get(db_session, id=file_id)
    #     logger.debug("Copying file: source %s | owner_id %s", source_file, owner_id)
    #     return await File.create(
    #         db_session,
    #         source_file.type,
    #         owner_id=owner_id,
    #         available=available,
    #         path=source_file.path,
    #         size=source_file.size,
    #         source_url=source_file.source_url,
    #     )
