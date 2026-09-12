from src.modules.schemas.episodes import EpisodeResponse
from src.modules.schemas.podcasts import PodcastResponse

from .auth import (
    AuthAccessTokenAPIController,
    AuthCoreAPIController,
    AuthInviteAPIController,
    AuthProfileAPIController,
    BaseAuthAPIController,
)
from .base import BaseApiController
from .cookies import CookieAPIController
from .episodes import EpisodeAPIController, PodcastEpisodeAPIController
from .media import MediaUploadAPIController
from .misc import PlaylistAPIController, ProgressAPIController, SystemAPIController
from .podcasts import PodcastAPIController

API_CONTROLLERS: tuple[type[BaseApiController], ...] = (
    AuthAccessTokenAPIController,
    AuthCoreAPIController,
    AuthInviteAPIController,
    AuthProfileAPIController,
    CookieAPIController,
    PodcastEpisodeAPIController,
    EpisodeAPIController,
    MediaUploadAPIController,
    PlaylistAPIController,
    ProgressAPIController,
    SystemAPIController,
    PodcastAPIController,
)

__all__ = (
    "BaseAuthAPIController",
    "AuthAccessTokenAPIController",
    "AuthCoreAPIController",
    "AuthInviteAPIController",
    "AuthProfileAPIController",
    "BaseApiController",
    "API_CONTROLLERS",
    "CookieAPIController",
    "PodcastAPIController",
    "PlaylistAPIController",
    "ProgressAPIController",
    "SystemAPIController",
    "PodcastResponse",
    "EpisodeAPIController",
    "PodcastEpisodeAPIController",
    "MediaUploadAPIController",
    "EpisodeResponse",
)
