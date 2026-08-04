from typing import Mapping, Sequence, Any

from sqladmin.forms import Form
from wtforms import BooleanField, PasswordField, StringField, widgets, EmailField

from src.modules.admin.constants import RENDER_KW_REQ, RENDER_KW


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


class LongTextAreaWidget(widgets.TextArea):
    def __call__(self, field, **kwargs):
        kwargs.setdefault("rows", 10)
        return super(LongTextAreaWidget, self).__call__(field, **kwargs)


class LongTextAreaField(StringField):
    """
    This field represents an HTML ``<textarea>`` and can be used to take
    multi-line input.
    """

    widget = LongTextAreaWidget()
