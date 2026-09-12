import logging
from typing import TypedDict, NotRequired, Callable, Mapping, Any, TYPE_CHECKING

from litestar.connection import Request
from litestar.datastructures import State

from src.modules.auth.types import TokenData
if TYPE_CHECKING:
    from src.modules.db import User

type AppRequest = Request[User, TokenData, State]
type AppRequestMayBeAuthenticated = Request[User | None, TokenData | None, State]


class YTDLParamsT(TypedDict):
    format: NotRequired[str]
    outtmpl: NotRequired[str]
    logger: NotRequired[logging.Logger]
    progress_hooks: NotRequired[list[Callable[[Mapping[str, Any]], None]]]
    noprogress: NotRequired[bool]
    cookiefile: NotRequired[str | None]
    noplaylist: NotRequired[bool]
    proxy: NotRequired[str]
