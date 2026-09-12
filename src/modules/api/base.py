from litestar import Controller

from src.constants import AuthSkip


class BaseApiController(Controller):
    opt = {
        AuthSkip.SKIP_AUTH_WEB: True,
    }
