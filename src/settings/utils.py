import logging
from typing import TypeVar

from pydantic_settings import BaseSettings
from pydantic_core import ValidationError

from src.exceptions import AppSettingsError

__all__ = ("prepare_settings",)

TypeSettings = TypeVar("TypeSettings", bound=BaseSettings)


def prepare_settings(settings_class: type[TypeSettings]) -> TypeSettings:
    """Prepares settings from environment variables"""
    try:
        settings: TypeSettings = settings_class()
    except ValidationError as exc:
        message = str(exc.errors(include_url=False, include_input=False))
        logging.debug("Unable to validate settings (caught Validation Error): \n %s", message)
        error_message = "Unable to validate settings: "
        for error in exc.errors():
            error_message += f"\n\t[{'|'.join(map(str, error['loc']))}] {error['msg']}"
        raise AppSettingsError(error_message) from exc

    except Exception as exc:
        logging.error("Unable to prepare settings (caught unexpected): \n %r", exc)
        raise AppSettingsError(f"Unable to prepare settings: {exc}") from exc

    return settings
