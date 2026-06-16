import logging
from datetime import timedelta

from litestar import Request, delete, get, patch, post
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.modules.api.base import BaseApiController
from exceptions import AuthInvalidAPIError, InvalidParametersAPIError, StateConflictAPIError

from src.modules.auth.backend import admin_user_guard
from src.modules.auth.tokens import (
    AuthTokenType,
    TokenPayload,
    create_user_session,
    decode_jwt,
    encode_jwt,
    refresh_user_session,
)
from src.modules.db.models import User, UserInvite
from src.modules.db.models.users import UserAccessToken
from src.modules.db.models.podcasts import Podcast
from src.modules.db.repositories import (
    PodcastRepository,
    UserAccessTokenRepository,
    UserInviteRepository,
    UserIPRepository,
    UserRepository,
    UserSessionRepository,
)
from src.modules.db.services import SASessionUOW
from src.modules.schemas.auth import (
    ChangePasswordRequest,
    CreatedUserAccessTokenResponse,
    DeleteUserIPsRequest,
    InviteUserRequest,
    ProfileUpdateRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    TokenResponse,
    UserAccessTokenCreateRequest,
    UserAccessTokenResponse,
    UserAccessTokenUpdateRequest,
    UserInviteResponse,
    UserIPResponse,
    UserResponse,
)
from src.modules.schemas.common import LimitOffsetPagination
from src.modules.services.email import _send_invitation_email, _send_reset_password_email
from src.settings.app import AppSettings
from src.utils import hash_string, utcnow

logger = logging.getLogger(__name__)


class BaseAuthAPIController(BaseApiController):
    """Shared auth API prefix and helpers for auth sub-controllers."""

    path = "/api/auth"
    tags = ["Auth"]

    @classmethod
    async def _register_user_ip(cls, request: Request, user: User, settings: AppSettings) -> None:
        """Best-effort IP history registration used by sign-in and profile requests."""
        address = request.headers.get(settings.request_ip_header)
        if not address and request.client:
            address = request.client.host

        hashed_address = hash_string(address or settings.default_request_user_ip)
        try:
            async with SASessionUOW() as uow:
                repository = UserIPRepository(uow.session)
                if await repository.first(user_id=user.id, hashed_address=hashed_address) is None:
                    await repository.create(
                        user_id=user.id,
                        hashed_address=hashed_address,
                        registered_by="",
                    )
                    uow.mark_for_commit()

        except Exception:
            logger.exception("[API] Failed to register IP for user #%s", user.id)


class AuthCoreAPIController(BaseAuthAPIController):

    @post("/sign-in/", auth_api_skip=True)
    async def sign_in(self, request: Request, settings: AppSettings) -> TokenResponse:
        """Authenticate a user and issue a token pair."""
        try:
            data = SignInRequest.model_validate(await request.json())
        except Exception as exc:
            raise InvalidParametersAPIError(details=str(exc)) from exc

        async with SASessionUOW() as uow:
            user_repo = UserRepository(uow.session)
            user = await user_repo.get_by_email(str(data.email))

        if user is None or not user.is_active or not user.verify_password(data.password):
            raise AuthInvalidAPIError(details="Email or password is invalid.")

        tokens = await create_user_session(user, settings=settings)
        await self._register_user_ip(request, user, settings=settings)
        logger.info("[API] User signed in: #%s", user.id)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @post("/refresh-token/", auth_api_skip=True)
    async def refresh_token(self, request: Request, settings: AppSettings) -> TokenResponse:
        """Refresh an access and refresh token pair."""
        try:
            data = RefreshTokenRequest.model_validate(await request.json())
        except Exception as exc:
            raise InvalidParametersAPIError(details=str(exc)) from exc

        tokens = await refresh_user_session(data.refresh_token, settings=settings)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @delete("/sign-out/", status_code=HTTP_200_OK)
    async def sign_out(self, current_user: User, request: Request) -> dict[str, bool]:
        """Deactivate the current authenticated API session."""
        api_auth = getattr(request.state, "api_auth", None)
        session_id: str | None = getattr(api_auth, "session_id", None)
        if session_id is not None:
            async with SASessionUOW() as uow:
                session_repo = UserSessionRepository(uow.session)
                await session_repo.deactivate_by_public_id(session_id)
                uow.mark_for_commit()

        logger.info("[API] User signed out: #%s", current_user.id)
        return {"ok": True}

    @post("/sign-up/", auth_api_skip=True, status_code=HTTP_201_CREATED)
    async def sign_up(self, data: SignUpRequest, settings: AppSettings) -> TokenResponse:
        """Create an invited user and issue a token pair."""
        async with SASessionUOW() as uow:
            user_repository = UserRepository(uow.session)
            if await user_repository.get_by_email(str(data.email)):
                raise StateConflictAPIError(
                    details=f"User with email '{data.email}' already exists."
                )

            invite_repository = UserInviteRepository(uow.session)
            invite = await invite_repository.get_valid(data.invite_token, str(data.email))
            if invite is None:
                raise InvalidParametersAPIError(
                    details="Invitation link is expired or unavailable."
                )

            user = await user_repository.create(
                email=str(data.email),
                password=User.make_password(data.password_1),
                is_active=True,
                is_superuser=False,
            )
            await uow.session.flush()
            await invite_repository.update(invite, is_applied=True, user_id=user.id)
            await PodcastRepository(uow.session).create(
                publish_id=Podcast.generate_publish_id(),
                name="Your podcast",
                description=(
                    "Add new episode -> wait for downloading -> copy podcast RSS-link "
                    "-> paste this link to your podcast application -> enjoy"
                ),
                owner_id=user.id,
            )
            uow.mark_for_commit()

        tokens = await create_user_session(user, settings=settings)
        logger.info("[API] User signed up: #%s", user.id)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @post("/reset-password/", auth_api_skip=True)
    async def reset_password(
        self,
        data: ResetPasswordRequest,
        settings: AppSettings,
    ) -> dict[str, bool]:
        """Send a password reset link without exposing account existence."""
        async with SASessionUOW() as uow:
            user = await UserRepository(uow.session).get_by_email(str(data.email))

        if user is not None and user.is_active:
            token, _ = encode_jwt(
                TokenPayload(user_id=user.id, token_type=AuthTokenType.RESET_PASSWORD),
                settings=settings,
                expires_in=settings.reset_password_link_expires_in,
            )
            await _send_reset_password_email(user, token, settings=settings)

        return {"ok": True}

    @post("/change-password/")
    async def change_password(
        self,
        data: ChangePasswordRequest,
        settings: AppSettings,
    ) -> dict[str, bool]:
        """Apply a password reset token and invalidate existing sessions."""
        payload = decode_jwt(
            data.token,
            expected_type=AuthTokenType.RESET_PASSWORD,
            settings=settings,
        )
        user_id = int(payload.get("user_id") or 0)
        if not user_id:
            raise AuthInvalidAPIError(details="Token payload misses user_id.")

        async with SASessionUOW() as uow:
            user_repository = UserRepository(uow.session)
            user = await user_repository.first(id=user_id, is_active=True)
            if user is None:
                raise AuthInvalidAPIError(
                    details="Password reset token owner is inactive or missing."
                )

            await user_repository.update(user, password=User.make_password(data.password_1))
            await UserSessionRepository(uow.session).deactivate_for_user(user.id)
            uow.mark_for_commit()

        return {"ok": True}


class AuthInviteAPIController(BaseAuthAPIController):

    @post("/invites/", status_code=HTTP_201_CREATED, guards=[admin_user_guard])
    async def invite_user(
        self,
        data: InviteUserRequest,
        current_user: User,
        settings: AppSettings,
    ) -> UserInviteResponse:
        """Create or refresh an invitation and send it by email."""
        email = str(data.email)
        async with SASessionUOW() as uow:
            if await UserRepository(uow.session).get_by_email(email):
                raise StateConflictAPIError(details=f"User with email '{email}' already exists.")

            invite_repository = UserInviteRepository(uow.session)
            invite = await invite_repository.first(email=email)
            token: str = UserInvite.generate_token()
            expired_at = utcnow() + timedelta(seconds=settings.invite_link_expires_in)
            if invite is None:
                invite = await invite_repository.create(
                    email=email,
                    owner_id=current_user.id,
                    token=token,
                    expired_at=expired_at,
                    is_applied=False,
                    user_id=None,
                )
            else:
                await invite_repository.update(
                    invite,
                    token=token,
                    expired_at=expired_at,
                    is_applied=False,
                    user_id=None,
                )

            await uow.session.flush()
            response = UserInviteResponse.model_validate(invite, from_attributes=True)
            uow.mark_for_commit()

        await _send_invitation_email(response, settings=settings)
        return response


class AuthProfileAPIController(BaseAuthAPIController):
    @get("/me/")
    async def me(
        self,
        request: Request,
        current_user: User,
        settings: AppSettings,
    ) -> UserResponse:
        """Return the current authenticated user."""
        await self._register_user_ip(request, current_user, settings=settings)
        return UserResponse.model_validate(current_user, from_attributes=True)

    @patch("/me/")
    async def update_me(self, data: ProfileUpdateRequest, current_user: User) -> UserResponse:
        """Update the authenticated user's email or password."""
        update_data: dict[str, str] = {}
        if data.email is not None and str(data.email) != current_user.email:
            update_data["email"] = str(data.email)
        if data.password_1 is not None:
            update_data["password"] = User.make_password(data.password_1)

        if update_data:
            async with SASessionUOW() as uow:
                user_repository = UserRepository(uow.session)
                if email := update_data.get("email"):
                    existing_user = await user_repository.get_by_email(email)
                    if existing_user is not None and existing_user.id != current_user.id:
                        raise StateConflictAPIError(
                            details=f"User with email '{email}' already exists."
                        )
                await user_repository.update(current_user, **update_data)
                uow.mark_for_commit()

        return UserResponse.model_validate(current_user, from_attributes=True)

    @get("/user-ips/")
    async def get_user_ips(
        self,
        current_user: User,
        limit: int = 10,
        offset: int = 0,
    ) -> LimitOffsetPagination[UserIPResponse]:
        """Return registered hashed addresses for the current user."""
        async with SASessionUOW() as uow:
            ips, total = await UserIPRepository(uow.session).all_paginated(
                user_id=current_user.id,
                limit=limit,
                offset=offset,
            )
        return LimitOffsetPagination[UserIPResponse](
            items=[UserIPResponse.model_validate(ip, from_attributes=True) for ip in ips],
            offset=offset,
            total=total,
        )

    @post("/user-ips/delete/")
    async def delete_user_ips(
        self,
        data: DeleteUserIPsRequest,
        current_user: User,
    ) -> dict[str, bool]:
        """Delete selected registered-address history entries."""
        async with SASessionUOW() as uow:
            repository = UserIPRepository(uow.session)
            ips = await repository.all(ids=data.ids, user_id=current_user.id)
            await repository.delete_by_ids([ip.id for ip in ips])
            uow.mark_for_commit()
        return {"ok": True}


class AuthAccessTokenAPIController(BaseAuthAPIController):
    @get("/access-tokens/")
    async def get_access_tokens(
        self,
        current_user: User,
        limit: int = 10,
        offset: int = 0,
    ) -> LimitOffsetPagination[UserAccessTokenResponse]:
        """Return long-lived API tokens without their stored hashes."""
        async with SASessionUOW() as uow:
            tokens, total = await UserAccessTokenRepository(uow.session).all_paginated(
                user_id=current_user.id,
                limit=limit,
                offset=offset,
            )
        return LimitOffsetPagination[UserAccessTokenResponse](
            items=[
                UserAccessTokenResponse.model_validate(token, from_attributes=True)
                for token in tokens
            ],
            offset=offset,
            total=total,
        )

    @post("/access-tokens/", status_code=HTTP_201_CREATED)
    async def create_access_token(
        self,
        data: UserAccessTokenCreateRequest,
        current_user: User,
    ) -> CreatedUserAccessTokenResponse:
        """Create a long-lived API token and show its raw value once."""
        raw_token = UserAccessToken.generate_token()
        async with SASessionUOW() as uow:
            access_token = await UserAccessTokenRepository(uow.session).create(
                user_id=current_user.id,
                token=hash_string(raw_token),
                name=data.name,
                expires_in=utcnow() + timedelta(days=data.expires_in_days),
            )
            await uow.session.flush()
            response = CreatedUserAccessTokenResponse(
                **UserAccessTokenResponse.model_validate(
                    access_token,
                    from_attributes=True,
                ).model_dump(),
                token=raw_token,
            )
            uow.mark_for_commit()
        return response

    @patch("/access-tokens/{token_id:int}/")
    async def update_access_token(
        self,
        token_id: int,
        data: UserAccessTokenUpdateRequest,
        current_user: User,
    ) -> UserAccessTokenResponse:
        """Rename, enable, or disable one of the current user's API tokens."""
        async with SASessionUOW() as uow:
            repository = UserAccessTokenRepository(uow.session)
            access_token = await repository.first(id=token_id, user_id=current_user.id)
            if access_token is None:
                raise InvalidParametersAPIError(details=f"Access token #{token_id} not found.")
            await repository.update(access_token, **data.model_dump(exclude_unset=True))
            uow.mark_for_commit()

        return UserAccessTokenResponse.model_validate(access_token, from_attributes=True)

    @delete("/access-tokens/{token_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete_access_token(self, token_id: int, current_user: User) -> None:
        """Delete one of the current user's API tokens."""
        async with SASessionUOW() as uow:
            repository = UserAccessTokenRepository(uow.session)
            access_token = await repository.first(id=token_id, user_id=current_user.id)
            if access_token is None:
                raise InvalidParametersAPIError(details=f"Access token #{token_id} not found.")
            await repository.delete(access_token)
            uow.mark_for_commit()


# Backward-compatible alias for tests and imports.
AuthAPIController = AuthCoreAPIController
