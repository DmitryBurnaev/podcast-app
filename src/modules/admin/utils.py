import datetime
import logging
import contextvars
from typing import TypedDict, Optional, Literal, cast, Any, TYPE_CHECKING

import markupsafe
from starlette.requests import Request

from src.constants import EpisodeStatus, SourceType
from src.modules.admin import constants
from src.settings.app import get_app_settings
from src.modules.db.models import BaseModel
from src.utils import get_invites_link
from utils import cut_string

if TYPE_CHECKING:
    from src.modules.db.models import UserInvite

logger = logging.getLogger(__name__)
alert_context_var: contextvars.ContextVar[Optional["ErrorInContext"]] = contextvars.ContextVar(
    "alert_context", default=None
)


class ErrorInContext(TypedDict):
    title: str
    details: str


def register_error_alert(title: str, details: str) -> None:
    """
    Register an error alert in the context
    """
    logger.debug("Registering error alert: title=%s, details=%s", title, details)
    alert_context_var.set(ErrorInContext(title=title, details=details))


def get_current_error_alert() -> dict[str, str] | None:
    """
    Get the current error alert from the context (used for global context in jinja templates)
    """
    current_error = alert_context_var.get()
    if current_error is None:
        return None

    return {
        "title": current_error["title"],
        "details": current_error["details"],
    }


def admin_get_link(
    instance: "BaseModel",
    url_name: str | None = None,
    target: Literal["edit", "details"] = "edit",
) -> str:
    """
    Simple helper function to generate a link to an instance
    (required for building items in admin's list view)

    :param instance: Some model's instance for link's building
    :param url_name: Part of url (admin path)
    :param target: Link target (edit / link)
    :return: HTML-safe tag with a generated link
    """
    settings = get_app_settings()
    base_url = settings.admin.base_url
    name = url_name or instance.admin_url_name
    instance_link = cut_string(str(instance), max_length=48)
    instance_title = " ".join(str(instance).replace('"', "").split(" ")[1:])
    return markupsafe.Markup(
        f'<a href="{base_url}/{name}/{target}/{instance.id}" title="{instance_title}">[#{instance.id}] {instance_link}</a>'
    )


def format_instance_details_link(model: Any, _: Any) -> str:
    return admin_get_link(cast(BaseModel, model), target="edit")


def _format_datetime(value: datetime.datetime | None, dt_format: str, blank: str) -> str:
    if not value:
        return blank

    ui_timezone = get_app_settings().ui_timezone
    if ui_timezone is not None:
        value = value.replace(tzinfo=datetime.timezone.utc).astimezone(ui_timezone)

    return value.strftime(dt_format)


def format_datetime(
    instance: "BaseModel", field_name: str, request: Request, *_, blank: str = "-"
) -> str:
    """
    Format a datetime object to a string in the format "%d.%m.%Y %H:%M"
    # instance: "BaseModel", field_name: str, blank: str = "-"
    """
    value = getattr(instance, field_name, None)
    if value is None:
        print("blank:", repr(blank))
        print("value:", repr(value))
        print("instance:", repr(instance))
        print("field_name:", repr(field_name))
        return blank

    return _format_datetime(value, dt_format="%d.%m.%Y %H:%M", blank=blank)


def format_bool(
    instance: "BaseModel",
    field_name: str,
    request: Request,
    *_,
    blank: str = "-",
) -> str:
    """Format a boolean object to an emoji symbol"""
    value: bool | None = getattr(instance, field_name, None)
    if value is None:
        return blank

    return {True: "✅", False: "❌"}[value]


def format_status(
    instance: "BaseModel", field_name: str, request: Request, *_, blank: str = "-"
) -> str:
    """Format a boolean object to an emoji symbol
    must be one of constants.EpisodeStatus
    :param instance: The instance of the model
    :param field_name: The name of the field to format
    :param blank: The blank string to return if the field is None
    :return: The formatted status
    """
    value: str | None = getattr(instance, field_name, None)
    if value is None:
        return blank

    emoj_map = {
        EpisodeStatus.PUBLISHED: "✅",
        EpisodeStatus.ERROR: "❌",
        EpisodeStatus.NEW: "🆕",
        EpisodeStatus.DOWNLOADING: "⬇️",
        EpisodeStatus.DL_PENDING: "⏳",
        EpisodeStatus.DL_EPISODE_DOWNLOADING: "⬇️",
        EpisodeStatus.DL_EPISODE_POSTPROCESSING: "🔄",
        EpisodeStatus.DL_EPISODE_UPLOADING: "⬆️",
        EpisodeStatus.DL_COVER_DOWNLOADING: "⬇️",
        EpisodeStatus.DL_COVER_UPLOADING: "⬆️",
        EpisodeStatus.CANCELING: "⏹️",
        EpisodeStatus.ARCHIVED: "📁",
    }
    return emoj_map.get(value, f"⚙️ ({value})")


def format_source_type(
    instance: "BaseModel",
    field_name: str,
    request: Request,
    *_,
    blank: str = "-",
) -> str:
    """Format a source type object to an emoji symbol
    must be one of constants.SourceType
    :param instance: The instance of the model
    :param field_name: The name of the field to format
    :param blank: The blank string to return if the field is None
    :return: The formatted source type
    """
    value: str | None = getattr(instance, field_name, None)
    if value is None:
        return blank

    emoj_map = {
        SourceType.YOUTUBE: "🎥",
        SourceType.YANDEX: "🎵",
        SourceType.UPLOAD: "📤",
    }
    return emoj_map.get(value, f"⚙️ ({value})")


def format_invite_link(instance: "BaseModel", field_name: str, blank: str = "-") -> str:
    """Generate a link to an invitation"""
    value: bool | None = getattr(instance, field_name, None)
    if value is None:
        return blank

    user_invite: "UserInvite" = cast("UserInvite", instance)
    settings = get_app_settings()
    if user_invite.email is None:
        logger.info("[admin] Email not provided for invite: %s", user_invite)
        return blank

    link = get_invites_link(email=user_invite.email, token=user_invite.token, settings=settings)
    return markupsafe.Markup(f'<a href="{link}">InviteLink</a>')
