"""Database module for the application."""

from src.modules.db.dependencies import (
    get_db_session,
    get_transactional_session,
    get_uow_with_session,
)
from src.modules.db.models import User
from src.modules.db.repositories import UserRepository
from src.modules.db.services import SASessionUOW
from src.modules.db.session import get_session_factory, initialize_database, close_database

__all__ = (
    # Models
    "User",
    # Repositories
    "UserRepository",
    # Services
    "SASessionUOW",
    # Session management
    "get_session_factory",
    "initialize_database",
    "close_database",
    "get_db_session",
    "get_transactional_session",
    "get_uow_with_session",
)
