from litestar import Controller

from src.modules.common.constants import AuthSkip

class BaseApiController(Controller):
    opt = {
        AuthSkip.SKIP_AUTH_WEB: True,
    }
