from functools import lru_cache, cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.utils import prepare_settings

__all__ = ("get_db_settings",)


class DBSettings(BaseSettings):
    """Implements settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="DB_")

    driver: str = "postgresql+asyncpg"
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    name: str = "code_agent"
    pool_min_size: int | None = Field(default_factory=lambda: None, description="Pool Min Size")
    pool_max_size: int | None = Field(default_factory=lambda: None, description="Pool Max Size")
    echo: bool = False

    @cached_property
    def database_dsn(self) -> str:
        return f"{self.driver}://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@lru_cache
def get_db_settings() -> DBSettings:
    """Prepares database settings from environment variables"""
    return prepare_settings(DBSettings)
