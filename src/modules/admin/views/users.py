import logging
from typing import cast, Any, Mapping, Sequence

from starlette.exceptions import HTTPException
from starlette.requests import Request
from wtforms import Form, StringField, EmailField, PasswordField, BooleanField

from src.modules.db import SASessionUOW, UserRepository
from src.modules.admin.views.base import BaseModelView, FormDataType
from src.modules.admin.constants import RENDER_KW_REQ
from src.modules.db.models import BaseModel, User
from src.modules.admin.utils import admin_get_link

__all__ = ("UserAdminView",)
logger = logging.getLogger(__name__)


class UserAdminForm(Form):
    """Provides extra validation for users' creation/updating"""

    username = StringField(render_kw=RENDER_KW_REQ, label="Username")
    email = EmailField(render_kw=RENDER_KW_REQ)
    new_password = PasswordField(render_kw={"class": "form-control"}, label="New Password")
    repeat_password = PasswordField(
        render_kw={"class": "form-control"},
        label="Repeat New Password",
    )
    is_admin = BooleanField(render_kw={"class": "form-check-input"})
    is_active = BooleanField(render_kw={"class": "form-check-input"})

    def validate(self, extra_validators: Mapping[str, Sequence[Any]] | None = None) -> bool:
        """Extra validation for user's form"""
        if new_password := self.data.get("new_password"):
            if new_password != self.data["repeat_password"]:
                self.new_password.errors = ("Passwords must be the same",)
                self.repeat_password.errors = ("Passwords must be the same",)
                return False

        return True


class UserAdminView(BaseModelView, model=User):
    """Provides logic for users' creation/updating"""

    form = UserAdminForm
    icon = "fa-solid fa-person-drowning"
    column_list = (User.id, User.email, User.is_active, User.is_superuser)
    column_details_list = (User.id, User.email, User.is_active, User.is_superuser)
    column_formatters = {User.email: lambda model, a: admin_get_link(cast(BaseModel, model))}

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
