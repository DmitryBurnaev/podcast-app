from functools import lru_cache

from litestar import Controller


class BaseApiController(Controller):
    @classmethod
    @lru_cache
    def get_controllers(cls) -> list[type["BaseApiController"]]:
        """Return concrete API controllers registered under this base controller."""
        return [controller for controller in cls.__subclasses__()]
