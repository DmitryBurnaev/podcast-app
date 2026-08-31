import logging
from typing import Any, TYPE_CHECKING, cast

from jinja2 import FileSystemLoader
from litestar import asgi
from litestar.types import Scope, Receive, Send
from sqladmin import Admin, BaseView, ModelView
from sqladmin.authentication import login_required
from starlette.applications import Starlette
from starlette.datastructures import FormData, URL
from starlette.requests import Request
from starlette.responses import Response

from src.constants import AuthSkip
from src.modules.admin.middlewares import PathFixMiddleware
from src.modules.admin.counters import AdminCounter
from src.modules.db import SASessionUOW
from src.modules.admin.auth import AdminAuth
from src.settings.app import APP_DIR
from src.modules.admin.utils import get_current_error_alert

from src.modules.admin.views import (
    BaseAPPView,
    BaseModelView,
    UserAdminView,
    UserInviteAdminView,
    UserSessionAdminView,
    UserIPAdminView,
    UserAccessTokenAdminView,
    PodcastAdminView,
    EpisodeAdminView,
    CookieAdminView,
    MediaFileAdminView,
)
from src.modules.db import session as db_session

if TYPE_CHECKING:
    from src.main import PodcastApp

ADMIN_VIEWS: tuple[type[BaseView], ...] = (
    UserAdminView,
    UserInviteAdminView,
    UserSessionAdminView,
    UserIPAdminView,
    UserAccessTokenAdminView,
    PodcastAdminView,
    EpisodeAdminView,
    CookieAdminView,
    MediaFileAdminView,
)

logger = logging.getLogger(__name__)


class AdminApp(Admin):
    """License-specific admin class."""

    custom_templates_dir = "modules/admin/templates"
    # app: "PodcastApp"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._starlette_app = Starlette()
        kwargs["app"] = self._starlette_app
        super().__init__(*args, **kwargs)
        self._init_jinja_templates()
        self._views: list[BaseModelView | BaseAPPView] = []  # type: ignore
        self._register_views()
        # disables redirecting based on absence/presence of trailing slashes
        self._starlette_app.add_middleware(PathFixMiddleware, base_url=self.base_url)
        self._starlette_app.router.redirect_slashes = False
        self.admin.router.redirect_slashes = False

    @login_required
    async def index(self, request: Request) -> Response:
        """Index route which can be overridden to create dashboards."""

        async with SASessionUOW() as uow:
            dashboard_stat = await AdminCounter().get_stat(session=uow.session)

        context = {
            "podcasts": {
                "total": dashboard_stat.total_podcasts,
                "active": 12,
            },
            "episodes": {
                "total": dashboard_stat.total_episodes,
            },
        }
        return await self.templates.TemplateResponse(request, "dashboard.html", context=context)

    @login_required
    async def create(self, request: Request) -> Response:
        """Create an admin model and render a custom post-create response when configured."""
        response: Response = await super().create(request)
        if request.method == "GET":
            return response

        # ==== prepare custom logic ====
        identity = request.path_params["identity"]
        model_view: "BaseModelView" = cast("BaseModelView", self._find_model_view(identity))
        if model_view.custom_post_create:
            object_id = int(response.headers["location"])
            response = await model_view.handle_post_create(request, object_id)
        # ====

        return response

    @staticmethod
    def get_save_redirect_url(
        request: Request,
        form: FormData,
        model_view: ModelView,
        obj: Any,
    ) -> str | URL:
        """
        Make more flexable getting redirect URL after saving model instance
        Allows fetching created instance's ID from formed redirect response (location header)
        We have to do this to avoid overriding whole `create` method of this class
        """

        redirect_url: str | URL
        if isinstance(model_view, BaseModelView) and model_view.custom_post_create:
            # required for getting instance ID after base creation's method finished
            redirect_url = str(obj.id)
        else:
            redirect_url = Admin.get_save_redirect_url(request, form, model_view, obj)

        return redirect_url

    @property
    def mount_path(self) -> str:
        """Return the admin mount path without a trailing slash."""
        return self.base_url.rstrip("/")

    def _init_jinja_templates(self) -> None:
        """
        Init jinja templates.
        Note: we have to insert loader in the start of list in order to override default templates
        """
        templates_dir = APP_DIR / self.custom_templates_dir
        self.templates.env.loader.loaders.insert(0, FileSystemLoader(templates_dir))  # type: ignore
        self.templates.env.globals["error_alert"] = get_current_error_alert

    def _register_views(self) -> None:
        for view in ADMIN_VIEWS:
            self.add_view(view)

        for view_instance in self._views:
            view_instance.app = cast("PodcastApp", cast(object, self.app))


def make_admin(app: "PodcastApp") -> Admin:
    """Create a simple admin application"""
    admin = AdminApp(
        base_url=app.settings.admin.base_url,
        title=app.settings.admin.title,
        session_maker=db_session.get_session_factory(),
        authentication_backend=AdminAuth(
            secret_key=app.settings.app_secret_key.get_secret_value(),
            settings=app.settings,
        ),
    )
    auth_opts: dict[str, bool] = {
        AuthSkip.SKIP_AUTH_API.value: True,
        AuthSkip.SKIP_AUTH_WEB.value: True,
    }

    @asgi(admin.mount_path, opt=auth_opts, is_mount=True)
    async def wrapped_app(scope: Scope, receive: Receive, send: Send) -> None:
        """Wrapper for the SQLAdmin app.

        Performs, and unwinds, the necessary scope modifications for the SQLAdmin app.
        """
        copied_scope = cast("Scope", cast(object, dict(scope)))
        copied_scope["path"] = f"{admin.mount_path}{scope['path']}"

        try:
            await admin._starlette_app(copied_scope, receive, send)  # type: ignore[arg-type]
        except Exception as err:
            logger.exception("Error raised from SQLAdmin app: %r", err)

    app.register(wrapped_app)
    return admin
