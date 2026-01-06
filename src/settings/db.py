from functools import lru_cache, cached_property
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.utils import prepare_settings

__all__ = (
    "get_db_settings",
    "get_s3_settings",
    "get_redis_settings",
    "DBSettings",
    "S3Settings",
    "RedisSettings",
)


class DBSettings(BaseSettings):
    """Database settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="DB_")

    driver: str = "postgresql+asyncpg"
    host: str | None = None
    port: int | None = None
    user: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    name: str = "podcast"
    pool_min_size: int = 1
    pool_max_size: int = 16
    ssl: str | None = None
    use_connection_for_request: bool = True
    retry_limit: int = 1
    retry_interval: int = 1
    echo: bool = False

    @cached_property
    def database_dsn(self) -> str:
        """Build database DSN from settings"""
        username = self.username or self.user or ""
        password = self.password.get_secret_value() if self.password else ""
        host = self.host or "localhost"
        port = self.port or 5432
        return f"{self.driver}://{username}:{password}@{host}:{port}/{self.name}"


class S3Settings(BaseSettings):
    """S3 storage settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="S3_")

    storage_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: SecretStr | None = None
    region_name: str | None = None
    bucket_name: str = "podcast"
    bucket_audio_path: str = "audio/"
    bucket_rss_path: str = "rss/"
    bucket_images_path: str = "images/"
    bucket_tmp_audio_path: str = "tmp/audio/"
    bucket_tmp_images_path: str = "tmp/images/"
    link_expires_in: int = Field(default=600, description="S3 link exp time in seconds")
    link_cache_expires_in: int = Field(default=120, description="S3 link cache exp time in seconds")

    @cached_property
    def bucket_episode_images_path(self) -> Path:
        """Path to episode images in S3 bucket"""
        return Path(self.bucket_images_path) / "episodes"

    @cached_property
    def bucket_podcast_images_path(self) -> Path:
        """Path to podcast images in S3 bucket"""
        return Path(self.bucket_images_path) / "podcasts"


class RedisSettings(BaseSettings):
    """Redis settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    max_connections: int = 10
    decode_responses: bool = True
    progress_pubsub_ch: str = "channel:episodes-progress"
    stop_downloading_pubsub_ch: str = "channel:episodes-stop-downloading"

    @cached_property
    def connection_tuple(self) -> tuple[str, int, int]:
        """Redis connection tuple (host, port, db)"""
        return self.host, self.port, self.db

    @cached_property
    def connection_dict(self) -> dict[str, str | int | bool]:
        """Redis connection dictionary"""
        return {
            "host": self.host,
            "port": self.port,
            "db": self.db,
            "max_connections": self.max_connections,
            "decode_responses": self.decode_responses,
        }

    @property
    def progress_pubsub_signal(self) -> str:
        """Signal name for progress pubsub"""
        return "EPISODES_UPDATED"

    @property
    def stop_downloading_pubsub_signal(self) -> str:
        """Signal name for stop downloading pubsub"""
        return "EPISODE_CANCEL_DOWNLOADING"


@lru_cache
def get_db_settings() -> DBSettings:
    """Prepares database settings from environment variables"""
    return prepare_settings(DBSettings)


@lru_cache
def get_s3_settings() -> S3Settings:
    """Prepares S3 settings from environment variables"""
    return prepare_settings(S3Settings)


@lru_cache
def get_redis_settings() -> RedisSettings:
    """Prepares Redis settings from environment variables"""
    return prepare_settings(RedisSettings)
