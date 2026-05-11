import logging

from litestar import Request, delete, get, post
from litestar.status_codes import HTTP_200_OK
from pydantic import BaseModel, EmailStr, Field

from src.modules.api.base import BaseApiController
from src.modules.api.errors import AuthInvalidError, InvalidParametersError
from src.modules.auth.tokens import create_user_session, refresh_user_session
from src.modules.db import User
from src.modules.db.repositories import UserRepository, UserSessionRepository
from src.modules.db.services import SASessionUOW

logger = logging.getLogger(__name__)


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    is_superuser: bool


class AuthAPIController(BaseApiController):
    path = "/api/auth"
    tags = ["Auth"]

    @post("/sign-in/")
    async def sign_in(self, request: Request) -> TokenResponse:
        try:
            data = SignInRequest.model_validate(await request.json())
        except Exception as exc:
            raise InvalidParametersError(details=str(exc)) from exc

        async with SASessionUOW() as uow:
            user_repo = UserRepository(uow.session)
            user = await user_repo.get_by_email(str(data.email))

        if user is None or not user.is_active or not user.verify_password(data.password):
            raise AuthInvalidError(details="Email or password is invalid.")

        tokens = await create_user_session(user)
        logger.info("[API] User signed in: #%s", user.id)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @post("/refresh-token/")
    async def refresh_token(self, request: Request) -> TokenResponse:
        try:
            data = RefreshTokenRequest.model_validate(await request.json())
        except Exception as exc:
            raise InvalidParametersError(details=str(exc)) from exc

        tokens = await refresh_user_session(data.refresh_token)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @delete("/sign-out/", status_code=HTTP_200_OK)
    async def sign_out(self, current_user: User, request: Request) -> dict[str, bool]:
        api_auth = getattr(request.state, "api_auth", None)
        session_id = getattr(api_auth, "session_id", None)
        if session_id:
            async with SASessionUOW() as uow:
                session_repo = UserSessionRepository(uow.session)
                await session_repo.deactivate_by_public_id(session_id)
                uow.mark_for_commit()

        logger.info("[API] User signed out: #%s", current_user.id)
        return {"ok": True}

    @get("/me/")
    async def me(self, current_user: User) -> UserResponse:
        return UserResponse.model_validate(current_user, from_attributes=True)
