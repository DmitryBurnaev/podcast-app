import logging
from functools import lru_cache
from typing import Annotated, TypedDict, Any

from pydantic import StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.utils import prepare_settings

__all__ = (
    "LOG_LEVELS_PATTERN",
    "LogSettings",
)

LOG_LEVELS_PATTERN = "DEBUG|INFO|WARNING|ERROR|CRITICAL"
LogLevelString = Annotated[
    str, StringConstraints(to_upper=True, pattern=rf"^(?i:{LOG_LEVELS_PATTERN})$")
]


class LogDictConfig(TypedDict):
    version: int
    disable_existing_loggers: bool
    formatters: dict[str, dict[str, str]]
    handlers: dict[str, dict[str, str | list[logging.Filter]]]
    loggers: dict[str, dict[str, list[str] | int | str | bool]]


class LoggingRequestForStaticsFilter(logging.Filter):
    """
    Simple filter for logging records: skip access to static files (like CSS/JS for admin panel)
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter out static access logs"""
        return "statics" not in record.getMessage().lower()


class LogSettings(BaseSettings):
    """Implements settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: LogLevelString = "INFO"
    sa_level: LogLevelString = "WARNING"
    skip_static_access: bool = False
    format: str = "[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)s] %(message)s"
    datefmt: str = "%d.%m.%Y %H:%M:%S"

    @property
    def dict_config(self) -> LogDictConfig:
        filters: list[logging.Filter] = []
        if self.skip_static_access:
            filters.append(LoggingRequestForStaticsFilter("skip-static-access"))

        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": self.format,
                    "datefmt": self.datefmt,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "filters": filters,
                }
            },
            "loggers": {
                "src.modules": {"handlers": ["console"], "level": self.level, "propagate": False},
                "app": {"handlers": ["console"], "level": self.level, "propagate": False},
                "uvicorn": {"handlers": ["console"], "level": self.level, "propagate": False},
                "yt_dlp": {"handlers": ["console"], "level": self.level, "propagate": False},
                "rq.worker": {"handlers": ["console"], "level": self.level, "propagate": False},
                "sqlalchemy.engine": {
                    "handlers": ["console"],
                    "level": self.sa_level,
                    "propagate": False,
                },
                "jinja2": {"handlers": ["console"], "level": self.level, "propagate": False},
            },
        }

    @property
    def dict_config_any(self) -> dict[str, Any]:
        """Just simple workaround for type checking in logging config"""
        return dict(self.dict_config)


@lru_cache
def get_log_settings() -> LogSettings:
    """Prepares logging settings from environment variables"""
    return prepare_settings(LogSettings)
