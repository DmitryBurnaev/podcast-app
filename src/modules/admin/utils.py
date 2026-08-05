import datetime
import logging
import contextvars
from typing import TypedDict, Optional, Literal, cast, Any

import markupsafe

from src.settings.app import get_app_settings
from src.modules.db.models import BaseModel

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
    return markupsafe.Markup(
        f'<a href="{base_url}/{name}/{target}/{instance.id}">[#{instance.id}] {instance}</a>'
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


def format_datetime(instance: "BaseModel", field_name: str, blank: str = "-", *_) -> str:
    """
    Format a datetime object to a string in the format "%d.%m.%Y %H:%M"
    # instance: "BaseModel", field_name: str, blank: str = "-"
    """
    value = getattr(instance, field_name, None)
    if value is None:
        return blank

    return _format_datetime(value, dt_format="%d.%m.%Y %H:%M", blank=blank)


def format_date(value: datetime.datetime, blank: str = "-") -> str:
    """
    Format a datetime object to a string in the format "%d.%m.%Y"
    """
    return _format_datetime(value, dt_format="%d.%m.%Y", blank=blank)


def format_bool(instance: "BaseModel", field_name: str, blank: str = "-") -> str:
    """ Format a boolean object to an emoj symbol """
    value: bool | None = getattr(instance, field_name, None)
    if value is None:
        return blank

    return {True: "✅", False: "❌"}[value]
