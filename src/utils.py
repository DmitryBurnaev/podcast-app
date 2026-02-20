import asyncio
import datetime
import hashlib
import logging
import unicodedata
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import TypeVar, Callable, ParamSpec, Any, Coroutine

import httpx

from src.settings.app import get_app_settings
from src.exceptions import NotFoundError

__all__ = ("singleton",)
logger = logging.getLogger(__name__)
T = TypeVar("T")
C = TypeVar("C")
P = ParamSpec("P")


def singleton(cls: type[C]) -> Callable[P, C]:
    """Class decorator that implements the Singleton pattern.

    This decorator ensures that only one instance of a class exists.
    All later instantiations will return the same instance.
    """
    instances: dict[str, C] = {}

    def getinstance(*args: P.args, **kwargs: P.kwargs) -> C:
        if cls.__name__ not in instances:
            instances[cls.__name__] = cls(*args, **kwargs)

        return instances[cls.__name__]

    return getinstance


# TODO: reimplement with litestar specific
# async def universal_exception_handler(request: "Request", exc: Exception) -> "JSONResponse":
#     """Universal exception handler that handles all types of exceptions"""
#
#     log_data: dict[str, str] = {
#         "error": "Internal server error",
#         "detail": str(exc),
#         "path": request.url.path,
#         "method": request.method,
#     }
#     log_level = logging.ERROR
#     status_code: int = 500
#
#     if isinstance(exc, BaseApplicationError):
#         log_level = exc.log_level
#         log_message = f"{exc.log_message}: {exc.message}"
#         status_code = exc.status_code
#         log_data |= {"error": exc.log_message, "detail": str(exc.message)}
#
#     elif isinstance(exc, (ValidationError,)):
#         log_level = logging.WARNING
#         log_message = f"Validation error: {str(exc)}"
#         status_code = 422
#         log_data |= {"error": log_message}
#
#     elif isinstance(exc, HTTPException):
#         log_level = logging.WARNING
#         status_code = exc.status_code
#         log_message = "Auth problem" if status_code == 401 else "Some http-related error"
#         log_message = f"{log_message}: {exc.detail}"
#         log_data |= {"error": log_message}
#
#     else:
#         log_message = f"Internal server error: {exc}"
#         log_data |= {
#             "detail": "An internal error has been detected. We apologize for the inconvenience."
#         }
#
#     exc_info = exc if logger.isEnabledFor(logging.DEBUG) else None
#     # Log the error
#     logger.log(log_level, log_message, extra=log_data, exc_info=exc_info)
#
#     return JSONResponse(
#         status_code=status_code,
#         content=ErrorResponse.model_validate(log_data).model_dump(),
#     )


def utcnow(skip_tz: bool = True) -> datetime.datetime:
    """Just a simple wrapper for deprecated datetime.utcnow"""
    dt = datetime.datetime.now(datetime.UTC)
    if skip_tz:
        dt = dt.replace(tzinfo=None)
    return dt


def decohints(decorator: Callable[..., Any]) -> Callable[..., Any]:
    """
    Small helper which helps to say IDE: "decorated method has the same params and return types"
    """
    return decorator


def simple_slugify(value: str) -> str:
    """
    Simple helper function to generate a slugified version of a string
    """
    return value.lower().strip().replace(" ", "-")


def cut_string(value: str | None, max_length: int = 128, placeholder: str = "...") -> str:
    """
    Simple helper function to cut a string with placeholder

    :param value: String to cut
    :param max_length: Maximum length of the string
    :param placeholder: Placeholder to add if the string is cut
    :return: Cut string

    >>> cut_string("Hello, world!")
    'Hello, world!'

    >>> cut_string("Hello, world!", max_length=5)
    'Hello...'

    >>> cut_string("Hello, world!", max_length=5, placeholder="")
    'Hello'

    >>> cut_string(None)
    ''

    """
    if not value:
        return ""

    return value[:max_length] + placeholder if len(value) > max_length else value


def is_basic_emoji(char: str) -> bool:
    try:
        # Check if the character's Unicode name contains 'EMOJI' or 'PICTOGRAM'
        # name = unicodedata.name(char)
        # print(char, name, unicodedata.category(char), sep=" | ")
        return unicodedata.category(char).lower() == "so"
    except ValueError:
        # Handles cases where a single char from a sequence has no name
        return False


# async def send_email(recipient_email: str, subject: str, html_content: str):
#     """Allows to send email via Sendgrid API"""
#
#     logger.debug("Sending email to: %s | subject: '%s'", recipient_email, subject)
#     required_settings = (
#         "SMTP_HOST",
#         "SMTP_PORT",
#         "SMTP_USERNAME",
#         "SMTP_PASSWORD",
#         "SMTP_FROM_EMAIL",
#     )
#     settings = get_app_settings()
#     if not all(getattr(settings, settings_name) for settings_name in required_settings):
#         raise ImproperlyConfiguredError(
#             f"SMTP settings: {required_settings} must be set for sending email"
#         )
#
#     smtp_client = aiosmtplib.SMTP(
#         hostname=settings.SMTP_HOST,
#         port=settings.SMTP_PORT,
#         use_tls=settings.SMTP_USE_TLS,
#         start_tls=settings.SMTP_STARTTLS,
#         username=settings.SMTP_USERNAME,
#         password=str(settings.SMTP_PASSWORD),
#     )
#
#     message = MIMEMultipart("alternative")
#     message["From"] = settings.SMTP_FROM_EMAIL
#     message["To"] = recipient_email
#     message["Subject"] = subject
#     message.attach(MIMEText(html_content, "html"))
#
#     async with smtp_client:
#         try:
#             smtp_details, smtp_status = await smtp_client.send_message(message)
#         except aiosmtplib.SMTPException as exc:
#             details = f"Couldn't send email: recipient: {recipient_email} | exc: {exc!r}"
#             raise EmailSendingError(details=details) from exc
#
#     if "OK" not in str(smtp_status):
#         details = f"Couldn't send email: {recipient_email=} | {smtp_status=} | {smtp_details=}"
#         raise EmailSendingError(details=details)
#
#     logger.info("Email sent to %s | subject: %s", recipient_email, subject)


def log_message(exc, error_data, level=logging.ERROR):
    """
    Helps to log caught errors by exception handler
    """
    error_details = {
        "error": error_data.get("error", "Unbound exception"),
        "details": error_data.get("details", str(exc)),
    }
    message = "{exc.__class__.__name__} '{error}': [{details}]".format(exc=exc, **error_details)
    logger.log(level, message, exc_info=(level == logging.ERROR))


#
# def custom_exception_handler(_, exc):
#     """
#     Returns the response that should be used for any given exception.
#     Response will be formatted by our format: {"error": "text", "detail": details}
#     """
#     error_message = "Something went wrong!"
#     error_details = f"Raised Error: {exc.__class__.__name__}"
#     status_code = getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)
#     response_status = ResponseStatus.INTERNAL_ERROR
#     if isinstance(exc, BaseApplicationError):
#         error_message = exc.message
#         error_details = exc.details
#         response_status = exc.response_status
#
#     elif isinstance(exc, WebargsHTTPException):
#         error_message = "Requested data is not valid."
#         error_details = exc.messages.get("json") or exc.messages.get("form") or exc.messages
#         status_code = status.HTTP_400_BAD_REQUEST
#         response_status = ResponseStatus.INVALID_PARAMETERS
#
#     payload = {"error": error_message}
#     if settings.APP_DEBUG or response_status == ResponseStatus.INVALID_PARAMETERS:
#         payload["details"] = error_details
#
#     response_data = {"status": response_status, "payload": payload}
#     log_level = logging.ERROR if httpx.codes.is_server_error(status_code) else logging.WARNING
#     log_message(exc, response_data["payload"], log_level)
#     return JSONResponse(response_data, status_code=status_code)


def hash_string(source_string: str) -> str:
    """
    Allows to limit source_string and append required sequence

    >>> hash_string('Some long string' * 10)
    '7421e493501b6a92f2a6884b93bf3f7ac7b479270753601941331d034d073d52
    >>> hash_string('127.0.0.1')
    '12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0'
    """
    return hashlib.sha256(source_string.encode()).hexdigest()


async def download_content(
    url: str, file_ext: str, retries: int = 5, sleep_retry: float = 0.1
) -> Path | None:
    """Allows fetching content from url"""

    logger.debug("Send request to %s", url)
    result_content = None
    retries += 1
    while retries := (retries - 1):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=600)
            except Exception as exc:
                logger.warning("Couldn't download %s! Error: %r", url, exc)
                await asyncio.sleep(sleep_retry)
                continue

            if response.status_code == HTTPStatus.NOT_FOUND:
                raise NotFoundError(f"Resource not found by URL {url}!")

            if not 200 <= response.status_code <= 299:
                logger.warning(
                    "Couldn't download %s | status: %s | response: %s",
                    url,
                    response.status_code,
                    response.text,
                )
                await asyncio.sleep(sleep_retry)
                continue

            result_content = response.content
            break

    if not result_content:
        raise NotFoundError(f"Couldn't download url {url} after {retries} retries.")

    settings = get_app_settings()
    path = settings.tmp_path / f"{uuid.uuid4().hex}.{file_ext}"
    with open(path, "wb") as file:
        file.write(result_content)

    return path


def create_task(
    coroutine: Coroutine[Any, Any, T],
    log_instance: logging.Logger,
    error_message: str = "",
    error_message_message_args: tuple[Any, ...] = (),
) -> asyncio.Task[T]:
    """Creates asyncio.Task from coro and provides logging for any exceptions"""

    def handle_task_result(cover_task: asyncio.Task) -> None:
        """Logging any exceptions after task finished"""
        try:
            cover_task.result()
        except asyncio.CancelledError:
            # Task cancellation should not be logged as an error.
            pass
        except Exception as exc:  # pylint: disable=broad-except
            if error_message:
                log_instance.exception(error_message, *error_message_message_args)
            else:
                log_instance.exception(f"Couldn't complete {coroutine.__name__}: %r", exc)

    task = asyncio.create_task(coroutine)
    task.add_done_callback(handle_task_result)
    return task
