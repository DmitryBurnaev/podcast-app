from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.utils import prepare_settings
from src.settings.log import LogSettings

__all__ = (
    "get_app_settings",
    "AppSettings",
)

APP_DIR = Path(__file__).parent.parent


class FlagsSettings(BaseSettings):
    """Implements settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_prefix="FLAG_")

    debug_mode: bool = False


class AppSettings(BaseSettings):
    """Application settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_docs_enabled: bool = False
    app_secret_key: SecretStr = Field(description="Application secret key")
    app_host: str = "localhost"
    app_port: int = 8003
    jwt_algorithm: str = "HS256"
    http_proxy_url: str | None = Field(default_factory=lambda: None, description="Socks5 Proxy URL")
    flags: FlagsSettings = Field(default_factory=FlagsSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    app_version: str = "1.0.0"
    sentry_dsn: str | None = None


@lru_cache
def get_app_settings() -> AppSettings:
    """Prepares application settings from environment variables"""
    return prepare_settings(AppSettings)


# SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]
