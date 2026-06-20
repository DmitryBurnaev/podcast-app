from datetime import datetime
from typing import NamedTuple, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.db.models import User


class TokenData(TypedDict, total=False):
    """
    payload["exp"] = expired_at
    payload["exp_iso"] = expired_at.isoformat()
    payload["token_type"] = str(token_type).lower()
    """

    exp: datetime
    exp_iso: str
    token_type: str
    user_id: int
    session_id: str


class ByTokenData(NamedTuple):
    user_id: int
    session_id: str = ""
    payload: TokenData | None = None


class AuthenticatedUserResult(NamedTuple):
    user: "User"
    token_data: TokenData | None = None
    session_id: str | None = None
