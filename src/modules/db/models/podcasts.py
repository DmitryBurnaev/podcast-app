import asyncio
import enum
import os
import logging
from pathlib import Path
import random
import uuid
from hashlib import md5
from types import MappingProxyType
from typing import NamedTuple, ClassVar
from functools import cached_property
from datetime import datetime, timedelta
from dataclasses import asdict, dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import SourceType
from src.modules.services.encryption import SensitiveData
from src.modules.db.models.media import File
from src.modules.db.models import BaseModel
from src.schemas import PodcastStatistics
from src.settings.app import get_app_settings
from src.utils import utcnow, cut_string, is_basic_emoji

logger = logging.getLogger(__name__)


class StringEnumMixin:
    __enum_name__: ClassVar[str] = NotImplemented
    __members__: MappingProxyType = NotImplemented

    @classmethod
    def members(cls) -> list[str]:
        return [str(status) for status in cls.__members__]


class EpisodeStatus(StringEnumMixin, enum.StrEnum):
    """Episode status enumeration"""

    __enum_name__ = "episode_status"

    NEW = "NEW"
    DOWNLOADING = "DOWNLOADING"
    CANCELING = "CANCELING"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    ERROR = "ERROR"

    # sub-statuses for DOWNLOADING (that statuses are not written to the DB, just for progress):
    DL_PENDING = "DL_PENDING"
    DL_EPISODE_DOWNLOADING = "DL_EPISODE_DOWNLOADING"
    DL_EPISODE_POSTPROCESSING = "DL_EPISODE_POSTPROCESSING"
    DL_EPISODE_UPLOADING = "DL_EPISODE_UPLOADING"
    DL_COVER_DOWNLOADING = "DL_COVER_DOWNLOADING"
    DL_COVER_UPLOADING = "DL_COVER_UPLOADING"

    @classmethod
    def members(cls) -> list[str]:
        return [status for status in cls.__members__ if not status.startswith("DL_")]


class Podcast(BaseModel):
    """SQLAlchemy schema for podcast instances"""

    __tablename__ = "podcast_podcasts"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    publish_id: Mapped[str] = mapped_column(sa.String(length=32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(length=256), nullable=False)
    description: Mapped[str] = mapped_column(sa.String)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    download_automatically: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    rss_id: Mapped[int] = mapped_column(sa.ForeignKey("media_files.id", ondelete="SET NULL"))
    image_id: Mapped[int] = mapped_column(sa.ForeignKey("media_files.id", ondelete="SET NULL"))
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("auth_users.id"))

    # relations
    rss: Mapped["File"] = relationship("File", foreign_keys=[rss_id], lazy="subquery")
    image: Mapped["File"] = relationship("File", foreign_keys=[image_id], lazy="subquery")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="podcast", lazy="subquery")

    def __str__(self):
        return f'<Podcast #{self.id} "{self.name}">'

    @property
    def stat(self) -> PodcastStatistics | None:
        return getattr(self, "_stat", None)

    @stat.setter
    def stat(self, value: PodcastStatistics):
        setattr(self, "_stat", value)

    @property
    def rss_url(self) -> str | None:
        if self.rss_id:
            return self.rss.url

        return None

    @property
    def image_url(self) -> str | None:
        app_settings = get_app_settings()
        url = self.image.url if self.image else None
        if not url and app_settings.default_podcast_cover:
            base_url = app_settings.service_url.removesuffix("/")
            url = f"{base_url}/{app_settings.default_podcast_cover}"

        return url

    # @classmethod
    # async def create_first_podcast(cls, db_session: AsyncSession, user_id: int):
    #     # TODO: move to repository
    #     return await Podcast.async_create(
    #         db_session,
    #         publish_id=cls.generate_publish_id(),
    #         name="Your podcast",
    #         description=(
    #             "Add new episode -> wait for downloading -> copy podcast RSS-link "
    #             "-> past this link to your podcast application -> enjoy".strip()
    #         ),
    #         owner_id=user_id,
    #     )

    @classmethod
    def generate_publish_id(cls) -> str:
        return md5(uuid.uuid4().hex.encode()).hexdigest()[::2]

    #
    # @property
    # def icon(self) -> str:
    #     """If 1st letter has emoji code, return it, otherwise return the first letter"""
    #     first_letter = self.name.strip()[0]
    #     if is_basic_emoji(first_letter):
    #         return first_letter
    #
    #     return "🎧"


@dataclass
class EpisodeChapter:
    """Base info about episode's chapter"""

    title: str
    start: int
    end: int

    @property
    def as_dict(self) -> dict:
        return asdict(self)  # noqa

    @property
    def start_str(self) -> str:  # ex.: 0:45:05
        return self._ftime(self.start)

    @property
    def end_str(self) -> str:  # ex.: 0:45:05
        return self._ftime(self.end)

    @staticmethod
    def _ftime(sec: int) -> str:
        result_delta: timedelta = timedelta(seconds=sec)
        mm, ss = divmod(result_delta.total_seconds(), 60)
        hh, mm = divmod(mm, 60)
        return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"  # 123sec -> '00:02:03'


class EpisodeMetadata(NamedTuple):
    podcast_name: str
    episode_id: int
    episode_title: str
    episode_author: str
    episode_chapters: list[EpisodeChapter]


class Episode(BaseModel):
    """SQLAlchemy schema for episode instances"""

    __tablename__ = "podcast_episodes"

    Status = EpisodeStatus
    Sources = SourceType
    PROGRESS_STATUSES = (EpisodeStatus.DOWNLOADING, EpisodeStatus.CANCELING)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(length=256), nullable=False)
    source_id: Mapped[str] = mapped_column(sa.String(length=32), index=True, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        sa.Enum(SourceType, name=SourceType.__enum_name__),
        default=SourceType.YOUTUBE,
        nullable=False,
    )
    podcast_id: Mapped[int] = mapped_column(
        sa.ForeignKey("podcast_podcasts.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    audio_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
    )
    image_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
    )
    owner_id: Mapped[int] = mapped_column(
        sa.ForeignKey("auth_users.id"), index=True, nullable=False
    )
    cookie_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("podcast_cookies.id", ondelete="SET NULL"), nullable=True
    )
    watch_url: Mapped[str | None] = mapped_column(sa.String(length=128), nullable=True)
    length: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    chapters: Mapped[list[dict] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )  # JSON list of `EpisodeChapter`
    author: Mapped[str | None] = mapped_column(sa.String(length=256), nullable=True)
    status: Mapped[EpisodeStatus] = mapped_column(
        sa.Enum(EpisodeStatus, name=EpisodeStatus.__enum_name__),
        default=EpisodeStatus.NEW,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    # relations
    podcast: Mapped["Podcast"] = relationship(back_populates="episodes", lazy="subquery")
    image: Mapped["File"] = relationship("File", foreign_keys=[image_id], lazy="subquery")
    audio: Mapped["File"] = relationship("File", foreign_keys=[audio_id], lazy="subquery")

    def __str__(self) -> str:
        return f'<Episode #{self.id} {self.source_id} [{self.status}] "{self.title[:10]}..." >'

    @classmethod
    async def get_in_progress(cls, db_session: AsyncSession, user_id: int):
        """Return downloading episodes"""
        # TODO: move to repository
        from sqlalchemy import select

        statement = (
            select(cls)
            .filter(cls.status.in_(Episode.PROGRESS_STATUSES))
            .filter(cls.owner_id == user_id)
        )
        result = await db_session.execute(statement)
        return list(result.scalars().all())

    @property
    def image_url(self) -> str | None:
        """Provides saved or the default one of episode's cover image"""
        app_settings = get_app_settings()
        url = self.image.url if self.image else None
        if not url and app_settings.default_episode_cover:
            base_url = app_settings.service_url.removesuffix("/")
            url = f"{base_url}/{app_settings.default_episode_cover}"

        return url

    @property
    def audio_url(self) -> str | None:
        """Public URL for episode audio file, if any."""
        if self.audio is None:
            return None
        return self.audio.url

    @property
    def list_chapters(self) -> list[EpisodeChapter]:
        """Converts currently saved chapters in DB to the list of chapter's objects"""

        def _ts(ts_as_str: str | int | float) -> int:
            if isinstance(ts_as_str, (float, int)):
                return int(ts_as_str)

            if not ts_as_str:
                return 0

            try:
                hours, minutes, seconds = map(int, ts_as_str.split(":"))
                return (hours * 3600) + (minutes * 60) + seconds

            except ValueError:
                return 0

        if not self.chapters:
            return []

        return [
            EpisodeChapter(
                title=chapter["title"], start=_ts(chapter["start"]), end=_ts(chapter["end"])
            )
            for chapter in self.chapters
        ]

    @property
    def rss_description(self) -> str:
        """Converts episode description to RSS format"""
        if not self.description:
            return ""

        cleared_description = self.description.replace("[LINK]", "")
        paragraphs = cleared_description.split("\n")
        result = ""
        for paragraph in paragraphs:
            if paragraph:
                result += f"<p>{paragraph}</p>"

        return result

    @cached_property
    def audio_filename(self) -> str:
        app_settings = get_app_settings()
        filename = self.audio.name if self.audio else ""
        if not filename or (self.audio and "tmp" in self.audio.path):
            suffix = md5(f"{self.source_id}-{app_settings.filename_salt}".encode()).hexdigest()
            _, ext = os.path.splitext(filename)
            filename = f"{self.source_id}_{suffix}{ext or '.mp3'}"

        return filename

    @property
    def short_description(self) -> str:
        return cut_string(self.description, max_length=150)

    @classmethod
    def generate_image_name(cls, source_id: str) -> str:
        return f"{source_id}_{uuid.uuid4().hex}.png"

    def generate_metadata(self) -> EpisodeMetadata:
        """Prepares common object for setting metadata to episode's audio file"""
        return EpisodeMetadata(
            episode_id=self.id,
            episode_title=self.title,
            episode_chapters=self.list_chapters,
            episode_author=self.author or "Unknown",
            podcast_name=self.podcast.name,
        )

    async def delete(self, db_session: AsyncSession, db_flush: bool = True):
        """Removing files associated with requested episode"""
        # TODO: move to repository
        raise NotImplementedError("Episode deletion not implemented")
        # app_settings = get_app_settings()
        #
        # if self.image_id and self.image:
        #     await self.image.delete(
        #         db_session, db_flush, remote_path=str(app_settings.s3.bucket_episode_images_path)
        #     )
        #
        # if self.audio_id and self.audio:
        #     await self.audio.delete(db_session, db_flush)
        #
        # await db_session.delete(self)
        # if db_flush:
        #     await db_session.flush()


class Cookie(BaseModel):
    """Saving cookies (in netscape format) for accessing to auth-only resources"""

    __tablename__ = "podcast_cookies"
    # __file_path: Path | None = None

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(
        sa.Enum(SourceType, name=SourceType.__enum_name__), nullable=False
    )
    data: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("auth_users.id"))

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.__file_path: Path | None = None

    def __str__(self) -> str:
        return f'<Cookie #{self.id} "{self.source_type}" at {self.created_at}>'

    async def as_file(self) -> Path:
        """Library for downloading content takes only path to cookie's file (stored on the disk)"""
        settings = get_app_settings()

        def store_tmp_file():
            cookies_file = settings.tmp_cookies_path / f"cookie_{self.source_type}_{self.id}.txt"
            if not cookies_file.exists():
                logger.debug("Cookie #%s: Generation tmp cookie file [%s]", self.id, cookies_file)
                with open(cookies_file, "wt", encoding="utf-8") as f:
                    decr_data = SensitiveData().decrypt(self.data)
                    f.write(decr_data)
            else:
                logger.debug("Cookie #%s: Found already generated file [%s]", self.id, cookies_file)

            return cookies_file

        return await asyncio.to_thread(store_tmp_file)

    @classmethod
    def get_encrypted_data(cls, data: str) -> str:
        """Return encrypted value for provided in `data` argument"""
        return SensitiveData().encrypt(data)

    @property
    def file_path(self) -> Path | None:
        return self.__file_path

    @file_path.setter
    def file_path(self, value: str | Path) -> None:
        self.__file_path = Path(value)
