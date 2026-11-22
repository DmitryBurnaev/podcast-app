from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from src.modules.admin.utils import admin_get_link, format_file_size, format_invite_link
from src.modules.admin.views import MediaFileAdminView, UserInviteAdminView
from src.modules.db.models import File


class TestAdminFormatters:
    def test_admin_link_escapes_model_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "src.modules.admin.utils.get_app_settings",
            lambda: SimpleNamespace(admin=SimpleNamespace(base_url="/admin")),
        )
        model = SimpleNamespace(
            id=1, admin_url_name="episode", admin_link_name="<script>x</script>"
        )

        result = admin_get_link(model)

        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert 'href="/admin/episode/edit/1"' in result

    def test_file_size_formatter__is_not_treated_as_request_aware(self) -> None:
        view = MediaFileAdminView()

        assert view._list_formatter_accepts_request["size"] is False
        assert format_file_size(SimpleNamespace(size=None), "size") == "-"

    def test_invite_link_formatter__is_not_treated_as_request_aware(self) -> None:
        view = UserInviteAdminView()

        assert view._list_formatter_accepts_request["token"] is False
        assert format_invite_link(SimpleNamespace(token=None), "token") == "-"

    def test_file_size_sort__puts_unknown_sizes_last(self) -> None:
        request = Request({"type": "http", "query_string": b"sortBy=size&sort=desc"})

        statement = MediaFileAdminView().sort_query(select(File), request)

        assert "ORDER BY media_files.size DESC NULLS LAST" in str(
            statement.compile(dialect=postgresql.dialect())
        )
