import logging
import contextvars
from typing import TypedDict, Optional

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
