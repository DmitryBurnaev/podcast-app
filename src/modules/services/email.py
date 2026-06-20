import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from src.exceptions import EmailSendingError, ImproperlyConfiguredError

from src.modules.db.models import User
from src.settings.app import AppSettings, get_app_settings
from src.utils import logger


async def _send_invitation_email(
    email: str,
    token: str,
    settings: AppSettings,
) -> None:
    invite_data = base64.urlsafe_b64encode(
        json.dumps({"token": token, "email": email}).encode()
    ).decode()
    link = f"{settings.site_url.rstrip('/')}/sign-up/?i={invite_data}"
    body = (
        f"<p>Hello! You have been invited to {settings.site_url}</p>"
        f"<p>Please follow the link:</p><p><a href='{link}'>{link}</a></p>"
    )
    await send_email(
        recipient_email=email,
        subject=f"Welcome to {settings.site_url}",
        html_content=body,
    )


async def _send_reset_password_email(user: User, token: str, settings: AppSettings) -> None:
    link = f"{settings.site_url.rstrip('/')}/change-password/?t={token}"
    body = (
        f"<p>You can reset your password for {settings.site_url}</p>"
        f"<p>Please follow the link:</p><p><a href='{link}'>{link}</a></p>"
    )
    await send_email(
        recipient_email=user.email,
        subject=f"Welcome back to {settings.site_url}",
        html_content=body,
    )


async def send_email(recipient_email: str, subject: str, html_content: str) -> None:
    """Send an HTML email through the configured SMTP server."""
    logger.debug("Sending email to: %s | subject: '%s'", recipient_email, subject)
    settings = get_app_settings().smtp
    required_settings = ("host", "port", "username", "password", "from_email")
    if not all(getattr(settings, name) for name in required_settings):
        raise ImproperlyConfiguredError(
            f"SMTP settings: {required_settings} must be set for sending email"
        )

    if settings.password is None:
        raise ImproperlyConfiguredError("SMTP password must be set for sending email")

    smtp_client = aiosmtplib.SMTP(
        hostname=settings.host,
        port=settings.port,
        use_tls=settings.use_tls,
        start_tls=settings.starttls,
        username=settings.username,
        password=settings.password.get_secret_value(),
    )
    message = MIMEMultipart("alternative")
    message["From"] = settings.from_email
    message["To"] = recipient_email
    message["Subject"] = subject
    message.attach(MIMEText(html_content, "html"))

    async with smtp_client:
        try:
            smtp_details, smtp_status = await smtp_client.send_message(message)
        except aiosmtplib.SMTPException as exc:
            details = f"Couldn't send email: recipient: {recipient_email} | exc: {exc!r}"
            raise EmailSendingError(details=details) from exc

    if "OK" not in str(smtp_status):
        details = f"Couldn't send email: {recipient_email=} | {smtp_status=} | {smtp_details=}"
        raise EmailSendingError(details=details)

    logger.info("Email sent to %s | subject: %s", recipient_email, subject)
