import logging
from typing import cast, Any, Mapping, Sequence

from starlette.exceptions import HTTPException
from starlette.requests import Request
from wtforms import Form, EmailField, PasswordField, BooleanField

from src.modules.db import SASessionUOW, UserRepository
from src.modules.db.models import UserInvite
from src.modules.admin.views.base import BaseModelView, FormDataType, mask_secret
from src.modules.admin.constants import RENDER_KW_REQ, RENDER_KW
from src.modules.db.models import User
from src.modules.admin.utils import format_instance_details_link

__all__ = ("UserAdminView",)
logger = logging.getLogger(__name__)


class UserAdminForm(Form):
    """Provides extra validation for users' creation/updating"""

    email = EmailField(render_kw=RENDER_KW_REQ)
    new_password = PasswordField(render_kw=RENDER_KW, label="New Password")
    repeat_password = PasswordField(render_kw=RENDER_KW, label="Repeat New Password")
    is_active = BooleanField(render_kw={"class": "form-check-input"})
    is_superuser = BooleanField(render_kw={"class": "form-check-input"})

    def validate(self, extra_validators: Mapping[str, Sequence[Any]] | None = None) -> bool:
        """Extra validation for user's form"""
        if new_password := self.data.get("new_password"):
            if new_password != self.data["repeat_password"]:
                self.new_password.errors = ("Passwords must be the same",)
                self.repeat_password.errors = ("Passwords must be the same",)
                return False

        return True


class UserInviteAdminForm(Form):
    """Provides extra validation for users' creation/updating"""

    email = EmailField(render_kw=RENDER_KW_REQ)


class UserAdminView(BaseModelView, model=User):
    """Provides logic for users' creation/updating"""

    form = UserAdminForm
    icon = "fa-solid fa-person-drowning"
    column_list = (User.id, User.email, User.is_active, User.is_superuser)
    column_details_list = (User.id, User.email, User.is_active, User.is_superuser)
    column_searchable_list = (User.email,)
    column_sortable_list = (User.id, User.email)
    column_default_sort = (User.id, True)
    column_formatters = {User.id: format_instance_details_link}

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
    form = UserInviteAdminForm
    column_list = [
        UserInvite.id,
        UserInvite.email,
        UserInvite.user_id,
        UserInvite.owner_id,
        UserInvite.is_applied,
        UserInvite.expired_at,
        UserInvite.created_at,
    ]
    column_details_exclude_list = [UserInvite.token]
    column_searchable_list = [UserInvite.email]
    column_sortable_list = [UserInvite.id, UserInvite.email, UserInvite.created_at]
    column_default_sort = (UserInvite.id, True)
    column_formatters = {"token": mask_secret, UserInvite.id: format_instance_details_link}
    column_formatters_detail = {"token": mask_secret}
