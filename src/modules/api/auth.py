import logging
from datetime import timedelta

from litestar import Request, delete, get, patch, post
from litestar.datastructures import Address
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.constants import AuthSkip
from src.modules.api.base import BaseApiController
from src.exceptions import (
    AuthInvalidAPIError,
    InvalidParametersAPIError,
    StateConflictAPIError,
    AuthenticationError,
)
from src.modules.auth.backend import admin_user_guard, APIAuthBackend
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
from src.modules.schemas.common import LimitOffsetPagination, OKResponse
from src.modules.services.email import _send_invitation_email, _send_reset_password_email
from src.modules.views.base import AppRequest
from src.settings.app import AppSettings
from src.utils import hash_string, utcnow

logger = logging.getLogger(__name__)


class BaseAuthAPIController(BaseApiController):
    """Shared auth API prefix and helpers for auth sub-controllers."""

    path = "/api/auth"
    tags = ["Auth"]

    @classmethod
    async def _register_user_ip(cls, request: AppRequest, settings: AppSettings) -> None:
        """Best-effort IP history registration used by sign-in and profile requests."""
        address = request.headers.get(settings.request_ip_header)
        if not address:
            client: Address = request.client or Address(
                host=settings.default_request_user_ip, port=0
            )
            address = client.host

        user = request.user
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

        except Exception as exc:
            logger.exception("[API] Failed to register IP for user #%s (%r)", user.id, exc)


class AuthCoreAPIController(BaseAuthAPIController):
    """Auth controllers for non-authenticated users"""

    opt = {
        AuthSkip.SKIP_AUTH_API: True,
        AuthSkip.SKIP_AUTH_WEB: True,
    }

    @post("/sign-in/")
    async def sign_in(
        self,
        request: Request,
        sign_in_data: SignInRequest,
    ) -> TokenResponse:
        """Authenticate a user and issue a token pair."""
        try:
            auth_backend = APIAuthBackend(request)
            success_login = await auth_backend.login(
                email=sign_in_data.email,
                password=sign_in_data.password,
            )
        except AuthenticationError as err:
            raise AuthInvalidAPIError(details=err.details) from err

        tokens = success_login.tokens
        if not tokens:
            raise AuthInvalidAPIError(details="Unable to continue: no token calculated")

        logger.info("[API] User signed in: #%s", success_login.user.id)
        return TokenResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        )

    @post("/sign-up/", status_code=HTTP_201_CREATED)
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

    @post("/refresh-token/")
    async def refresh_token(self, request: Request, settings: AppSettings) -> TokenResponse:
        """Refresh an access and refresh token pair."""
        try:
            data = RefreshTokenRequest.model_validate(await request.json())
        except Exception as exc:
            raise InvalidParametersAPIError(details=str(exc)) from exc

        tokens = await refresh_user_session(data.refresh_token, settings=settings)
        return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)

    @post("/reset-password/")
    async def reset_password(self, data: ResetPasswordRequest, settings: AppSettings) -> OKResponse:
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

        return OKResponse()

    @post("/change-password/")
    async def change_password(
        self,
        data: ChangePasswordRequest,
        settings: AppSettings,
    ) -> OKResponse:
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

        return OKResponse()


class AuthExtendedAPIController(BaseAuthAPIController):
    """Auth controllers which requires authentication."""

    @delete("/sign-out/", status_code=HTTP_200_OK)
    async def sign_out(self, current_user: User, request: Request) -> OKResponse:
        """Deactivate the current authenticated API session."""
        api_auth = getattr(request.state, "api_auth", None)
        session_id: str | None = getattr(api_auth, "session_id", None)
        if session_id is not None:
            async with SASessionUOW() as uow:
                session_repo = UserSessionRepository(uow.session)
                await session_repo.deactivate_by_public_id(session_id)
                uow.mark_for_commit()

        logger.info("[API] User signed out: #%s", current_user.id)
        return OKResponse()


class AuthInviteAPIController(BaseAuthAPIController):
    @get("/invites/", guards=[admin_user_guard])
    async def get_invites(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> LimitOffsetPagination[UserInviteResponse]:
        """Return paginated user invitations."""
        async with SASessionUOW() as uow:
            user_invite_repo = UserInviteRepository(uow.session)
            invites, total = await user_invite_repo.all_paginated(limit=limit, offset=offset)

        return LimitOffsetPagination[UserInviteResponse](
            items=[
                UserInviteResponse.model_validate(invite, from_attributes=True)
                for invite in invites
            ],
            offset=offset,
            total=total,
        )

    @post("/invites/", status_code=HTTP_201_CREATED, guards=[admin_user_guard])
    async def create_invite(
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
            token: str = UserInvite.generate_token()
            expired_at = utcnow() + timedelta(seconds=settings.invite_link_expires_in)
            invite = await invite_repository.first(email=email)
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

        await _send_invitation_email(email, token, settings=settings)
        return response


class AuthProfileAPIController(BaseAuthAPIController):
    @get("/me/")
    async def me(
        self,
        request: AppRequest,
        settings: AppSettings,
    ) -> UserResponse:
        """Return the current authenticated user."""
        # await self._register_user_ip(request, settings=settings)
        return UserResponse.model_validate(request.user, from_attributes=True)

    @patch("/me/")
    async def update_me(self, data: ProfileUpdateRequest, request: AppRequest) -> UserResponse:
        """Update the authenticated user's email or password."""
        update_data: dict[str, str] = {}
        if data.email is not None and str(data.email) != request.user.email:
            update_data["email"] = str(data.email)
        if data.password_1 is not None:
            update_data["password"] = User.make_password(data.password_1)

        if update_data:
            async with SASessionUOW() as uow:
                user_repository = UserRepository(uow.session)
                if email := update_data.get("email"):
                    existing_user = await user_repository.get_by_email(email)
                    if existing_user is not None and existing_user.id != request.user.id:
                        raise StateConflictAPIError(
                            details=f"User with email '{email}' already exists."
                        )

                await user_repository.update(request.user, **update_data)
                uow.mark_for_commit()

        return UserResponse.model_validate(request.user, from_attributes=True)

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
    ) -> OKResponse:
        """Delete selected registered-address history entries."""
        async with SASessionUOW() as uow:
            repository = UserIPRepository(uow.session)
            ips = await repository.all(ids=data.ids, user_id=current_user.id)
            await repository.delete_by_ids([ip.id for ip in ips])
            uow.mark_for_commit()

        return OKResponse()


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
