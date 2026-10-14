from src.modules.web_api.episodes import (
    EpisodeWebController,
    PodcastEpisodeWebController,
)
from src.modules.web_api.podcasts import PodcastWebController
from src.modules.web_api.progress import ProgressWebController

WEB_API_CONTROLLERS = (
    PodcastWebController,
    PodcastEpisodeWebController,
    EpisodeWebController,
    ProgressWebController,
)
