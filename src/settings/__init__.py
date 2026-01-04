from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from .app import AppSettings
from .utils import prepare_settings

__all__ = (
    "AppSettings",
    "SettingsDep",
    "get_app_settings",
)


@lru_cache
def get_app_settings() -> AppSettings:
    """Prepares application settings from environment variables"""
    return prepare_settings(AppSettings)


SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]
