import logging
from datetime import timedelta
from typing import cast, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request

from src.modules.services.email import send_invitation_email
from src.modules.db.repositories import UserInviteRepository
from src.settings.app import get_app_settings
from src.modules.admin.forms import UserAdminForm, ReadOnlyTextField
from src.modules.db import SASessionUOW, UserRepository
from src.modules.db.models import UserInvite
from src.modules.admin.views.base import BaseModelView, FormDataType
from src.modules.db.models import User
from src.utils import utcnow
from src.modules.admin.utils import (
    format_instance_details_link,
    format_datetime,
    format_bool,
    format_invite_link,
)

__all__ = ("UserAdminView",)
logger = logging.getLogger(__name__)


class UserAdminView(BaseModelView, model=User):
    """Provides logic for users' creation/updating"""

    form = UserAdminForm
    icon = "fa-solid fa-person-drowning"
    column_list = (User.id, User.is_active, User.is_superuser)
    column_details_list = (User.id, User.email, User.is_active, User.is_superuser)
    column_searchable_list = (User.email,)
    column_sortable_list = (User.id, User.email)
    column_default_sort = (User.id, True)
    column_formatters = {
        User.id: format_instance_details_link,
        User.is_active: format_bool,
        User.is_superuser: format_bool,
    }
    column_labels = {
        User.id: "User",
        User.is_active: "Active",
        User.is_superuser: "Admin",
    }

    async def insert_model(self, request: Request, data: FormDataType) -> Any:
        """Create a new user and insert it into the database"""

        raw_password: str | None = str(data.get("new_password") or "")
        if raw_password:
            data["password"] = User.make_password(str(raw_password))
        else:
            raise HTTPException(status_code=400, detail="Password required")

        await self._validate_email(email=cast(str, data.get("email")))

        return await super().insert_model(request, data)

    async def update_model(self, request: Request, pk: str, data: FormDataType) -> Any:
        """Update an existing user and insert it into the database (username can't be changed)"""

        data.pop("username", None)
        raw_password = data.pop("new_password", None)
        data.pop("repeat_password", None)
        if raw_password:
            data["password"] = User.make_password(str(raw_password))

        return await super().update_model(request, pk, data)

    @staticmethod
    async def _validate_email(email: str) -> None:
        async with SASessionUOW() as uow:
            user_repo = UserRepository(session=uow.session)
            exists_user = await user_repo.get_by_email(email)
            if exists_user is not None:
                raise HTTPException(status_code=400, detail="Username already taken")


class UserInviteAdminView(BaseModelView, model=UserInvite):
    name = "Invite"
    name_plural = "Invites"
    icon = "fa-solid fa-envelope-open-text"
    column_list = (
        UserInvite.id,
        UserInvite.is_applied,
        UserInvite.token,
        UserInvite.expired_at,
        UserInvite.created_at,
    )
    form_columns = (
        UserInvite.id,
        UserInvite.email,
        # UserInvite.token,
        UserInvite.is_applied,
        UserInvite.expired_at,
    )
    form_overrides = {"token": ReadOnlyTextField}
    column_searchable_list = (UserInvite.email,)
    column_sortable_list = (UserInvite.id, UserInvite.email, UserInvite.created_at)
    column_default_sort = (UserInvite.id, True)
    column_formatters = {
        UserInvite.id: format_instance_details_link,
        UserInvite.is_applied: format_bool,
        UserInvite.created_at: format_datetime,
        UserInvite.expired_at: format_datetime,
        UserInvite.token: format_invite_link,
    }
    column_labels = {
        UserInvite.id: "Invite",
        UserInvite.token: "Token",
        UserInvite.is_applied: "Applied",
        UserInvite.expired_at: "Expired At",
        UserInvite.created_at: "Created At",
    }

    async def insert_model(self, request: Request, data: FormDataType) -> Any:
        """Create a new invite if email isn't already taken"""
        email = data.get("email", None)
        async with SASessionUOW() as uow:
            invite_repository = UserInviteRepository(uow.session)
            invite = await invite_repository.first(email=email)
            if invite is not None:
                raise HTTPException(status_code=400, detail="Email already taken")

        settings = get_app_settings()
        token: str = UserInvite.generate_token()
        expired_at = utcnow() + timedelta(seconds=settings.invite_link_expires_in)
        data.update(
            {
                "token": token,
                "expired_at": expired_at,
            }
        )
        invite = await super().insert_model(request, data)
        await send_invitation_email(
            email=invite.email,
            token=invite.token,
            settings=settings,
        )
        return invite
