from functools import lru_cache

from litestar import Controller


class BaseApiController(Controller):
    @classmethod
    @lru_cache
    def get_controllers(cls) -> list[type["BaseApiController"]]:
        return [controller for controller in cls.__subclasses__()]
