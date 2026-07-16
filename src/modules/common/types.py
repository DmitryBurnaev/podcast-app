from litestar.connection import Request
from litestar.datastructures import State

from src.modules.auth.types import TokenData
from src.modules.db import User

type AppRequest = Request[User, TokenData, State]
type AppRequestMayBeAuthenticated = Request[User | None, TokenData | None, State]
