from wtforms import BooleanField, DateTimeLocalField, IntegerField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, Length, Optional

from sqladmin.forms import Form


class UserAdminForm(Form):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=128)])
    new_password = PasswordField("New password", validators=[Optional(), Length(min=8, max=128)])
    is_active = BooleanField("Active")
    is_superuser = BooleanField("Superuser")


class CookieAdminForm(Form):
    source_type = StringField("Source type", validators=[DataRequired()])
    raw_data = StringField("Cookie data", validators=[Optional()])
    owner_id = IntegerField("Owner ID", validators=[DataRequired()])


class UserAccessTokenAdminForm(Form):
    user_id = IntegerField("User ID", validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired(), Length(max=256)])
    new_token = StringField("Access token", validators=[Optional(), Length(max=256)])
    enabled = BooleanField("Enabled")
    expires_in = DateTimeLocalField(
        "Expires at", validators=[DataRequired()], format="%Y-%m-%dT%H:%M"
    )
