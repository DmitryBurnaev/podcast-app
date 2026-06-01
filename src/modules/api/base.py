from functools import lru_cache

from litestar import Controller


class BaseApiController(Controller):
    @classmethod
    @lru_cache
    def get_controllers(cls) -> list[type["BaseApiController"]]:
        """Return leaf API controllers registered under this base controller."""
        controllers: list[type[BaseApiController]] = []

        def collect(controller_cls: type[BaseApiController]) -> None:
            subclasses = controller_cls.__subclasses__()
            if not subclasses:
                controllers.append(controller_cls)
                return
            for subclass in subclasses:
                collect(subclass)

        for subclass in cls.__subclasses__():
            collect(subclass)

        return controllers
