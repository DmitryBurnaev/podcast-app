import logging

from http import HTTPStatus


class BaseApplicationError(Exception):
    """Base application error"""

    log_level: int = logging.ERROR
    log_message: str = "Application error"
    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AppSettingsError(BaseApplicationError):
    """Settings error"""


class StartupError(BaseApplicationError):
    """Startup error"""


class DatabaseError(BaseApplicationError):
    """Database error"""
