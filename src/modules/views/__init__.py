from .index import IndexController
from .media import MediaByTokenController
from .podcasts import PodcastsController, PodcastCoverController, PodcastsDetailsController
from .episodes import (
    EpisodesController,
    EpisodeCoverController,
    EpisodeCreateFragmentController,
    EpisodeDetailsController,
)
from .system import AboutController
from .auth import AuthLoginController, AuthLogoutController
from .users import ProfileController
from .progress import ProgressEventsController

from .base import BaseViewController

VIEW_CONTROLLERS: tuple[type[BaseViewController], ...] = (
    IndexController,
    MediaByTokenController,
    EpisodesController,
    EpisodeCreateFragmentController,
    ProgressEventsController,
    EpisodeCoverController,
    EpisodeDetailsController,
    PodcastsController,
    PodcastsDetailsController,
    PodcastCoverController,
    AuthLoginController,
    AuthLogoutController,
    ProfileController,
    AboutController,
)

__all__ = (
    "IndexController",
    "MediaByTokenController",
    "EpisodesController",
    "EpisodeCoverController",
    "EpisodeDetailsController",
    "PodcastsController",
    "PodcastsDetailsController",
    "PodcastCoverController",
    "AuthLoginController",
    "AuthLogoutController",
    "ProfileController",
    "AboutController",
    "VIEW_CONTROLLERS",
)
