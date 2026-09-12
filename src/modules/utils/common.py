import asyncio
import base64
import datetime
import hashlib
import json
import re
import uuid
import logging
import dataclasses
from pathlib import Path
from http import HTTPStatus
from typing import (
    NamedTuple,
    TypedDict,
    Callable,
    Any,
    cast,
    TypeVar,
    ParamSpec,
    TYPE_CHECKING,
)

import httpx
import yt_dlp
from yt_dlp.utils import YoutubeDLError

from src.settings.app import AppSettings, get_app_settings
from src.modules.common.types import YTDLParamsT
from src.modules.common.constants import SourceType
from src.modules.common.exceptions import InvalidRequestError, NotFoundError
from src.modules.auth.hashers import get_random_hash

if TYPE_CHECKING:
    from src.modules.dto.podcasts import EpisodeChapter

logger = logging.getLogger(__name__)


class SourceMediaInfo(NamedTuple):
    """Structure of extended information about media source"""

    watch_url: str
    source_id: str
    description: str
    thumbnail_url: str
    title: str
    author: str
    length: int
    chapters: list["EpisodeChapter"]


@dataclasses.dataclass
class SourceConfig:
    type: SourceType
    regexp: str | None = None
    regexp_playlist: str | None = None
    need_postprocessing: bool = False
    need_downloading: bool = True

    @property
    def proxy_url(self) -> str | None:
        """Return a configured proxy URL for source requests that require one."""
        if self.type is SourceType.YOUTUBE:
            settings = get_app_settings()
            return settings.http_proxy_url

        return None


@dataclasses.dataclass
class SourceInfo:
    id: str
    type: SourceType
    url: str | None = None
    cookie_path: Path | None = None
    proxy_url: str | None = None


SOURCE_CFG_MAP = {
    SourceType.YOUTUBE: SourceConfig(
        type=SourceType.YOUTUBE,
        regexp=(
            r"^https://(?:www\.)?"
            r"[(?:youtube\.com)|(?:youtu\.be)]+[(/watch\?v=|\/)|(/live/)]+"
            r"(?P<source_id>[0-9a-zA-Z-_]{11})"
        ),
        regexp_playlist=(
            r"^https://(?:www\.)?youtube\.com/playlist\?list=(?P<source_id>[0-9a-zA-Z-_]+)"
        ),
        need_postprocessing=True,
    ),
    SourceType.YANDEX: SourceConfig(
        type=SourceType.YANDEX,
        regexp=r"https?://music\.yandex\.ru\/[a-z\/0-9]+\/track\/(?P<source_id>[0-9]+)",
        regexp_playlist=r"^https://music\.yandex\.ru/album/(?P<source_id>[0-9a-zA-Z-_]+)",
    ),
    SourceType.UPLOAD: SourceConfig(
        type=SourceType.UPLOAD,
        need_downloading=False,
    ),
}


def extract_source_info(source_url: str | None = None, playlist: bool = False) -> SourceInfo:
    """Extracts providers (source) info and finds source ID"""
    logger.info(f"Extracting source information from {source_url}")
    if not source_url:
        random_hash = get_random_hash(size=6)
        return SourceInfo(id=f"U-{random_hash}", type=SourceType.UPLOAD)

    for _, source_cfg in SOURCE_CFG_MAP.items():
        regexp = source_cfg.regexp if not playlist else source_cfg.regexp_playlist
        if match := (re.match(regexp, source_url) if regexp else None):
            if source_id := match.groupdict().get("source_id"):
                return SourceInfo(id=source_id, url=source_url, type=source_cfg.type)

            logger.error(
                "Couldn't extract source ID: Source link is not correct: %s | source_info: %s",
                source_url,
                source_cfg,
            )

    raise InvalidRequestError(f"Requested domain is not supported now {source_url}")


class SourceDetails(TypedDict):
    title: str
    description: str | None
    webpage_url: str
    id: str
    thumbnail: str
    uploader: str | None
    artist: str | None
    duration: int
    chapters: list[dict] | None


async def get_source_media_info(source_info: SourceInfo) -> tuple[str, SourceMediaInfo | None]:
    """Allows extract info about providers video from Source (powered by yt_dlp)"""

    logger.info("Started fetching data for %s", source_info.url)
    params: YTDLParamsT = {
        "logger": logger,
        "noplaylist": True,
        "cookiefile": str(source_info.cookie_path) if source_info.cookie_path else None,
    }
    if source_info.proxy_url:
        params["proxy"] = source_info.proxy_url
        logger.info("YoutubeDL: Using proxy: %s", source_info.proxy_url)

    if source_info.url is None:
        return "Source URL is not specified", None

    try:
        with yt_dlp.YoutubeDL(params) as ydl:  # type: ignore
            source_details = cast(
                SourceDetails,
                await asyncio.to_thread(
                    ydl.extract_info,
                    source_info.url,
                    download=False,
                ),
            )

    except YoutubeDLError as exc:
        logger.exception("ydl.extract_info failed: %s | Error: %r", source_info.url, exc)
        return str(exc), None

    author: str = source_details.get("uploader", "") or source_details.get("artist") or "unknown"
    youtube_info = SourceMediaInfo(
        title=source_details["title"],
        description=source_details.get("description") or source_details.get("title"),
        watch_url=source_details["webpage_url"],
        source_id=source_details["id"],
        thumbnail_url=source_details["thumbnail"],
        author=author,
        length=source_details["duration"],
        chapters=chapters_processing(source_details.get("chapters")),
    )
    return "OK", youtube_info


def chapters_processing(input_chapters: list[dict] | None) -> list[EpisodeChapter]:
    """
    Allows to process input chapters data and adapt to internal chapter's format
    (for saving in DB and using in RSS generation)

    input:
        [{'end_time': 68.0, 'start_time': 15.0, 'title': 'Start application'}, ...]
    output:
        [EpisodeChapter(title='Start application', start='00:00:15', end='00:01:08'), ...]

    :param input_chapters: list of chapters data
    :return: list of chapters items
    """
    result_chapters: list[EpisodeChapter] = []
    if not input_chapters:
        return []

    for input_chapter in input_chapters:
        try:
            chapter = EpisodeChapter(
                title=input_chapter["title"],
                start=input_chapter["start_time"],
                end=input_chapter["end_time"],
            )

        except (KeyError, ValueError) as exc:
            logger.error("Couldn't prepare episode's chapter: %s | err: %r", input_chapter, exc)

        else:
            result_chapters.append(chapter)

    return result_chapters


T = TypeVar("T")
C = TypeVar("C")
P = ParamSpec("P")


def singleton(cls: type[C]) -> Callable[P, C]:
    """Class decorator that implements the Singleton pattern.

    This decorator ensures that only one instance of a class exists.
    All later instantiations will return the same instance.
    """
    instances: dict[str, C] = {}

    def getinstance(*args: P.args, **kwargs: P.kwargs) -> C:
        if cls.__name__ not in instances:
            instances[cls.__name__] = cls(*args, **kwargs)

        return instances[cls.__name__]

    return getinstance


def utcnow(skip_tz: bool = True) -> datetime.datetime:
    """Just a simple wrapper for deprecated datetime.utcnow"""
    dt = datetime.datetime.now(datetime.UTC)
    if skip_tz:
        dt = dt.replace(tzinfo=None)
    return dt


def decohints(decorator: Callable[..., Any]) -> Callable[..., Any]:
    """
    Small helper which helps to say IDE: "decorated method has the same params and return types"
    """
    return decorator


def simple_slugify(value: str) -> str:
    """
    Simple helper function to generate a slugified version of a string
    """
    return value.lower().strip().replace(" ", "-")


def cut_string(value: str | None, max_length: int = 128, placeholder: str = "...") -> str:
    """
    Simple helper function to cut a string with placeholder

    :param value: String to cut
    :param max_length: Maximum length of the string
    :param placeholder: Placeholder to add if the string is cut
    :return: Cut string

    >>> cut_string("Hello, world!")
    'Hello, world!'

    >>> cut_string("Hello, world!", max_length=5)
    'Hello...'

    >>> cut_string("Hello, world!", max_length=5, placeholder="")
    'Hello'

    >>> cut_string(None)
    ''

    """
    if not value:
        return ""

    return value[:max_length] + placeholder if len(value) > max_length else value


def hash_string(source_string: str) -> str:
    """
    Allows to limit source_string and append required sequence

    >>> hash_string('Some long string' * 10)
    '7421e493501b6a92f2a6884b93bf3f7ac7b479270753601941331d034d073d52
    >>> hash_string('127.0.0.1')
    '12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0'
    """
    return hashlib.sha256(source_string.encode()).hexdigest()


def get_invites_link(
    email: str,
    token: str,
    settings: AppSettings,
) -> str:
    """"""
    invite_data = base64.urlsafe_b64encode(
        json.dumps({"token": token, "email": email}).encode()
    ).decode()
    link = f"{settings.site_url.rstrip('/')}/sign-up/?i={invite_data}"
    return link


async def download_content(
    url: str, file_ext: str, retries: int = 5, sleep_retry: float = 0.1
) -> Path | None:
    """Allows fetching content from url"""

    logger.debug("Send request to %s", url)
    result_content = None
    retries += 1
    settings = get_app_settings()
    while retries := (retries - 1):
        async with httpx.AsyncClient(proxy=settings.http_proxy_url) as client:
            try:
                response = await client.get(url, timeout=600)
            except Exception as exc:
                logger.warning("Couldn't download %s! Error: %r", url, exc)
                await asyncio.sleep(sleep_retry)
                continue

            if response.status_code == HTTPStatus.NOT_FOUND:
                raise NotFoundError(f"Resource not found by URL {url}!")

            if not 200 <= response.status_code <= 299:
                logger.warning(
                    "Couldn't download %s | status: %s | response: %s",
                    url,
                    response.status_code,
                    response.text,
                )
                await asyncio.sleep(sleep_retry)
                continue

            result_content = response.content
            break

    if not result_content:
        raise NotFoundError(f"Couldn't download url {url} after {retries} retries.")

    settings = get_app_settings()
    path = settings.tmp_path / f"{uuid.uuid4().hex}.{file_ext}"
    with open(path, "wb") as file:
        file.write(result_content)

    return path
