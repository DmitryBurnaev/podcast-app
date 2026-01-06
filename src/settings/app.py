import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.db import DBSettings, RedisSettings, S3Settings
from src.settings.utils import prepare_settings
from src.settings.log import LogSettings

__all__ = (
    "get_app_settings",
    "AppSettings",
    "SMTPSettings",
)

APP_DIR = Path(__file__).parent.parent


class FlagsSettings(BaseSettings):
    """Implements settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_prefix="FLAG_")

    debug_mode: bool = False


class SMTPSettings(BaseSettings):
    """SMTP settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_prefix="SMTP_")

    host: str | None = None
    port: int = 465
    username: str | None = None
    password: SecretStr | None = None
    starttls: bool | None = None
    use_tls: bool = True
    from_email: str = ""


class AppSettings(BaseSettings):
    """Application settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # core app settings
    app_secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr("podcast-app-secret!"),
        description="Application secret key",
    )
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_version: str = "latest"

    # nested settings:
    flags: FlagsSettings = Field(default_factory=FlagsSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    smtp: SMTPSettings = Field(default_factory=SMTPSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    s3: S3Settings = Field(default_factory=S3Settings)

    # other settings
    api_docs_enabled: bool = False
    jwt_algorithm: str = "HS512"
    jwt_expires_in: int = Field(default=5 * 60, description="JWT token expiration time in seconds")
    jwt_refresh_expires_in: int = Field(
        default=30 * 24 * 3600, description="JWT refresh token expiration time in seconds"
    )
    http_proxy_url: str | None = Field(default_factory=lambda: None, description="Socks5 Proxy URL")
    render_links: bool = True
    episode_chapters_title_length: int = 65
    sentry_dsn: str | None = None
    sens_data_encrypt_key: str | None = None
    site_url: str = "https://podcast.site.com"
    service_url: str = "https://podcast-service.site.com/"
    default_request_user_ip: str = "127.0.0.1"
    request_ip_header: str = "X-Real-IP"
    default_pagination_limit: int = 20
    default_limit_list_api: int = 20
    filename_salt: str = "HH78NyP4EXsGy99"
    email_from: str = Field(default="", description="Default email from address")
    invite_link_expires_in: int = Field(
        default=3 * 24 * 3600,
        description="Invite link expiration time in seconds",
    )
    reset_password_link_expires_in: int = Field(
        default=3 * 3600,
        description="Reset password link expiration time in seconds",
    )
    download_event_redis_ttl: int = Field(
        default=60 * 60,
        description="Download event Redis TTL in seconds",
    )
    rq_queue_name: str = "podcast"
    rq_default_timeout: int = Field(default=24 * 3600, description="RQ default timeout in seconds")
    ffmpeg_timeout: int = Field(default=2 * 60 * 60, description="FFmpeg timeout in seconds")
    max_upload_attempt: int = 5
    max_upload_audio_filesize: int = Field(
        default=1024 * 1024 * 512,
        description="Max upload audio filesize in bytes",
    )
    max_upload_image_filesize: int = Field(
        default=1024 * 1024 * 10,
        description="Max upload image filesize in bytes",
    )
    retry_upload_timeout: int = 1
    default_episode_cover: str = "episode-default.jpg"
    default_podcast_cover: str = "podcast-default.jpg"

    @property
    def template_path(self) -> Path:
        """Path to templates directory"""
        return APP_DIR / "templates"

    @property
    def tmp_path(self) -> Path:
        """Temporary directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast__"))

    @property
    def tmp_audio_path(self) -> Path:
        """Temporary audio directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast_audio__"))

    @property
    def tmp_rss_path(self) -> Path:
        """Temporary RSS directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast_rss__"))

    @property
    def tmp_image_path(self) -> Path:
        """Temporary image directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast_images__"))

    @property
    def tmp_cookies_path(self) -> Path:
        """Temporary cookies directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast_cookies__"))

    @property
    def tmp_meta_path(self) -> Path:
        """Temporary metadata directory path"""
        return Path(tempfile.mkdtemp(prefix="podcast_meta__"))


@lru_cache
def get_app_settings() -> AppSettings:
    """Prepares application settings from environment variables"""
    return prepare_settings(AppSettings)


# SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]
