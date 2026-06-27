from typing import Any

from starlette.requests import Request
from sqladmin import ModelView
from sqladmin.secret import Secret

from src.modules.admin.forms import CookieAdminForm, UserAccessTokenAdminForm, UserAdminForm
from src.modules.db.models import File, User, UserAccessToken, UserInvite
from src.modules.db.models.podcasts import Cookie, Episode, Podcast
from src.utils import hash_string, utcnow

MASKED_SECRET = "********"


def mask_secret(_: type, __: Any) -> str:
    return MASKED_SECRET


class SecureModelView(ModelView):
    """Shared admin defaults for model views."""

    page_size = 25
    page_size_options = [25, 50, 100]
    can_view_details = True
    can_export = True

    def is_accessible(self, request: Request) -> bool:
        return bool(request.session.get("admin_user_id"))

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)


class UserAdmin(SecureModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.email, User.is_active, User.is_superuser]
    column_details_list = [User.id, User.email, User.is_active, User.is_superuser]
    column_searchable_list = [User.email]
    column_sortable_list = [User.id, User.email]
    column_filters = [User.is_active, User.is_superuser]
    column_default_sort = (User.id, True)
    column_export_list = [User.id, User.email, User.is_active, User.is_superuser]
    form = UserAdminForm

    async def insert_model(self, request: Request, data: dict[str, Any]) -> User:
        raw_password = str(data.pop("new_password") or "")
        if not raw_password:
            raise ValueError("New password is required.")

        user = User(
            email=data["email"],
            password=User.make_password(raw_password),
            is_active=bool(data.get("is_active")),
            is_superuser=bool(data.get("is_superuser")),
        )
        async with self.session_maker(expire_on_commit=False) as session:
            session.add(user)
            await session.commit()
        return user

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> User:
        raw_password = str(data.pop("new_password") or "")
        async with self.session_maker(expire_on_commit=False) as session:
            user = await session.scalar(self._stmt_by_identifier(pk))
            if user is None:
                raise ValueError("User not found.")

            user.email = data["email"]
            user.is_active = bool(data.get("is_active"))
            user.is_superuser = bool(data.get("is_superuser"))
            if raw_password:
                user.password = User.make_password(raw_password)

            await session.commit()
            return user


class UserInviteAdmin(SecureModelView, model=UserInvite):
    name = "User Invite"
    name_plural = "User Invites"
    icon = "fa-solid fa-envelope-open-text"
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
    column_filters = [UserInvite.is_applied, UserInvite.email]
    column_default_sort = (UserInvite.id, True)
    column_formatters = {"token": mask_secret}
    column_formatters_detail = {"token": mask_secret}


class PodcastAdmin(SecureModelView, model=Podcast):
    name = "Podcast"
    name_plural = "Podcasts"
    icon = "fa-solid fa-podcast"
    column_list = [
        Podcast.id,
        Podcast.name,
        Podcast.publish_id,
        Podcast.owner_id,
        Podcast.download_automatically,
        Podcast.created_at,
    ]
    column_searchable_list = [Podcast.name, Podcast.publish_id]
    column_sortable_list = [Podcast.id, Podcast.name, Podcast.created_at]
    column_filters = [Podcast.download_automatically, Podcast.owner_id]
    column_default_sort = (Podcast.id, True)


class EpisodeAdmin(SecureModelView, model=Episode):
    name = "Episode"
    name_plural = "Episodes"
    icon = "fa-solid fa-headphones"
    column_list = [
        Episode.id,
        Episode.title,
        Episode.status,
        Episode.source_type,
        Episode.podcast_id,
        Episode.owner_id,
        Episode.created_at,
        Episode.published_at,
    ]
    column_searchable_list = [Episode.title, Episode.source_id, Episode.watch_url]
    column_sortable_list = [Episode.id, Episode.title, Episode.created_at, Episode.published_at]
    column_filters = [Episode.status, Episode.source_type, Episode.owner_id, Episode.podcast_id]
    column_default_sort = (Episode.id, True)


class CookieAdmin(SecureModelView, model=Cookie):
    name = "Cookie"
    name_plural = "Cookies"
    icon = "fa-solid fa-cookie-bite"
    can_export = False
    column_list = [
        Cookie.id,
        Cookie.source_type,
        Cookie.owner_id,
        Cookie.created_at,
        Cookie.updated_at,
    ]
    column_details_list = [
        Cookie.id,
        Cookie.source_type,
        Cookie.owner_id,
        Cookie.created_at,
        Cookie.updated_at,
    ]
    column_sortable_list = [Cookie.id, Cookie.source_type, Cookie.created_at, Cookie.updated_at]
    column_filters = [Cookie.source_type, Cookie.owner_id]
    column_default_sort = (Cookie.id, True)
    column_formatters = {"data": mask_secret}
    column_formatters_detail = {"data": mask_secret}
    form = CookieAdminForm

    async def insert_model(self, request: Request, data: dict[str, Any]) -> Cookie:
        raw_data = str(data.pop("raw_data") or "")
        if not raw_data:
            raise ValueError("Cookie data is required.")

        cookie = Cookie(
            source_type=data["source_type"],
            data=Cookie.get_encrypted_data(raw_data),
            owner_id=int(data["owner_id"]),
        )
        async with self.session_maker(expire_on_commit=False) as session:
            session.add(cookie)
            await session.commit()
        return cookie

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Cookie:
        raw_data = str(data.pop("raw_data") or "")
        async with self.session_maker(expire_on_commit=False) as session:
            cookie = await session.scalar(self._stmt_by_identifier(pk))
            if cookie is None:
                raise ValueError("Cookie not found.")

            cookie.source_type = data["source_type"]
            cookie.owner_id = int(data["owner_id"])
            cookie.updated_at = utcnow()
            if raw_data:
                cookie.data = Cookie.get_encrypted_data(raw_data)

            await session.commit()
            return cookie


class MediaFileAdmin(SecureModelView, model=File):
    name = "Media File"
    name_plural = "Media Files"
    icon = "fa-solid fa-file-audio"
    column_list = [
        File.id,
        File.type,
        File.path,
        File.size,
        File.available,
        File.public,
        File.owner_id,
    ]
    column_searchable_list = [File.path, File.source_url, File.hash]
    column_sortable_list = [File.id, File.path, File.size, File.created_at]
    column_filters = [File.type, File.available, File.public, File.owner_id]
    column_default_sort = (File.id, True)
    column_formatters = {"access_token": mask_secret}
    column_formatters_detail = {"access_token": mask_secret}


class UserAccessTokenAdmin(SecureModelView, model=UserAccessToken):
    name = "User Access Token"
    name_plural = "User Access Tokens"
    icon = "fa-solid fa-key"
    can_export = False
    column_list = [
        UserAccessToken.id,
        UserAccessToken.user_id,
        UserAccessToken.name,
        UserAccessToken.enabled,
        UserAccessToken.expires_in,
        UserAccessToken.created_at,
    ]
    column_details_list = [
        UserAccessToken.id,
        UserAccessToken.user_id,
        UserAccessToken.name,
        UserAccessToken.enabled,
        UserAccessToken.expires_in,
        UserAccessToken.created_at,
    ]
    column_searchable_list = [UserAccessToken.name]
    column_sortable_list = [UserAccessToken.id, UserAccessToken.name, UserAccessToken.created_at]
    column_filters = [UserAccessToken.enabled, UserAccessToken.user_id]
    column_default_sort = (UserAccessToken.id, True)
    column_formatters = {"token": mask_secret}
    column_formatters_detail = {"token": mask_secret}
    form = UserAccessTokenAdminForm

    async def insert_model(self, request: Request, data: dict[str, Any]) -> UserAccessToken:
        raw_token = str(data.pop("new_token") or "") or UserAccessToken.generate_token()
        access_token = UserAccessToken(
            user_id=int(data["user_id"]),
            name=data["name"],
            token=hash_string(raw_token),
            enabled=bool(data.get("enabled")),
            expires_in=data["expires_in"],
        )
        async with self.session_maker(expire_on_commit=False) as session:
            session.add(access_token)
            await session.commit()

        Secret.reveal_once(
            request,
            raw_token,
            title="User access token",
            label="Copy this access token now. It will not be shown again.",
        )
        return access_token

    async def update_model(
        self,
        request: Request,
        pk: str,
        data: dict[str, Any],
    ) -> UserAccessToken:
        raw_token = str(data.pop("new_token") or "")
        async with self.session_maker(expire_on_commit=False) as session:
            access_token = await session.scalar(self._stmt_by_identifier(pk))
            if access_token is None:
                raise ValueError("Access token not found.")

            access_token.user_id = int(data["user_id"])
            access_token.name = data["name"]
            access_token.enabled = bool(data.get("enabled"))
            access_token.expires_in = data["expires_in"]
            if raw_token:
                access_token.token = hash_string(raw_token)
                Secret.reveal_once(
                    request,
                    raw_token,
                    title="User access token",
                    label="Copy this access token now. It will not be shown again.",
                )

            await session.commit()
            return access_token


ADMIN_VIEWS: tuple[type[ModelView], ...] = (
    UserAdmin,
    UserInviteAdmin,
    PodcastAdmin,
    EpisodeAdmin,
    CookieAdmin,
    MediaFileAdmin,
    UserAccessTokenAdmin,
)
