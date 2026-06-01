import base64
import json

from src.modules.db.models import User
from src.modules.schemas.auth import UserInviteResponse
from src.settings.app import AppSettings
from src.utils import send_email


async def _send_invitation_email(invite: UserInviteResponse, settings: AppSettings) -> None:
    invite_data = base64.urlsafe_b64encode(
        json.dumps({"token": invite.token, "email": str(invite.email)}).encode()
    ).decode()
    link = f"{settings.site_url.rstrip('/')}/sign-up/?i={invite_data}"
    body = (
        f"<p>Hello! You have been invited to {settings.site_url}</p>"
        f"<p>Please follow the link:</p><p><a href='{link}'>{link}</a></p>"
    )
    await send_email(
        recipient_email=str(invite.email),
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
