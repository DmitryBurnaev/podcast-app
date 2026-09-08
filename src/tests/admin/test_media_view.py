from types import SimpleNamespace
from pathlib import Path
from typing import Self
from unittest.mock import ANY, AsyncMock, create_autospec

from jinja2 import Environment
import pytest
from starlette.datastructures import URL
from starlette.requests import Request

from src.modules.admin.views import MediaFileAdminView
from src.modules.admin.views.base import BaseModelView
from src.modules.admin.utils import alert_context_var, get_current_error_alert
from src.modules.db.repositories import FileRepository
from src.modules.services.storage import (
    FileCleanupBatchResult,
    FileCleanupItemResult,
    FileCleanupStatus,
)
from src.tests.factories import make_episode, make_file


class FakeUOW:
    """Minimal async UoW used to inject repository doubles into the admin view."""

    session = object()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def use_file_repository(
    monkeypatch: pytest.MonkeyPatch,
    repository: FileRepository,
) -> None:
    """Route MediaFileAdminView database access through one repository double."""
    monkeypatch.setattr("src.modules.admin.views.media.SASessionUOW", FakeUOW)
    monkeypatch.setattr(
        "src.modules.admin.views.media.FileRepository",
        lambda *, session: repository,
    )


class TestMediaFileAdminView:
    def test_edit_form__shows_episodes_linked_to_audio_or_image(self) -> None:
        audio_file = make_file(id=1)
        image_file = make_file(id=2)
        audio_episode = make_episode(id=10)
        image_episode = make_episode(id=20)

        audio_episode.audio = audio_file
        image_episode.image = image_file

        assert audio_file.audio_episodes == [audio_episode]
        assert image_file.image_episodes == [image_episode]
        assert MediaFileAdminView.list_template == "media_list.html"
        assert MediaFileAdminView.edit_template == "media_edit.html"

    @pytest.mark.parametrize(
        ("s3_env", "badge_class"),
        (("dev", "bg-green-lt"), ("prod", "bg-red-lt")),
    )
    def test_s3_admin_context__shows_safe_host_bucket_and_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        s3_env: str,
        badge_class: str,
    ) -> None:
        monkeypatch.setattr(
            "src.modules.admin.views.media.get_app_settings",
            lambda: SimpleNamespace(
                s3=SimpleNamespace(
                    storage_url="https://access:secret@s3.example.test:9443/private?token=hidden",
                    bucket_name="podcast-bucket",
                    env=s3_env,
                )
            ),
        )

        context = MediaFileAdminView().s3_admin_context

        assert context.host == "s3.example.test:9443"
        assert context.bucket == "podcast-bucket"
        assert context.badge_label == s3_env.upper()
        assert context.badge_class == badge_class
        assert "secret" not in context.host
        assert "private" not in context.host
        assert "token" not in context.host

    def test_s3_admin_context__shows_warning_when_storage_url_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "src.modules.admin.views.media.get_app_settings",
            lambda: SimpleNamespace(
                s3=SimpleNamespace(
                    storage_url=None,
                    bucket_name="podcast-bucket",
                    env="prod",
                )
            ),
        )

        context = MediaFileAdminView().s3_admin_context

        assert context.host == "No Config"
        assert context.bucket == "podcast-bucket"
        assert context.badge_label == "NO CONFIG"
        assert context.badge_class == "bg-yellow-lt"

    def test_s3_content_label__shows_bucket_and_relative_object_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "src.modules.admin.views.media.get_app_settings",
            lambda: SimpleNamespace(
                s3=SimpleNamespace(
                    bucket_name="podcast bucket",
                )
            ),
        )
        file = make_file(path="audio/my file#1.mp3")

        label = MediaFileAdminView().s3_content_label(file)

        assert label == "podcast bucket/audio/my file#1.mp3"

    def test_s3_content_label__is_missing_without_path(
        self,
    ) -> None:
        assert MediaFileAdminView().s3_content_label(make_file(path="")) is None

    def test_media_templates__parse_with_shared_storage_context(self) -> None:
        templates_dir = Path("src/modules/admin/templates")
        environment = Environment()

        for template_name in (
            "snippets/storage_context.html",
            "media_list.html",
            "media_edit.html",
        ):
            environment.parse((templates_dir / template_name).read_text())

        edit_template = (templates_dir / "media_edit.html").read_text()
        assert edit_template.index(">Episodes<") < edit_template.index(">Content<")
        assert ">Same-path files<" in edit_template
        assert 'target="_blank"' in edit_template
        assert 'class="btn btn-secondary" disabled' in edit_template

    async def test_get_object_for_edit__loads_other_files_with_the_same_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        media_file = make_file(id=7, path="audio/shared.mp3")
        same_path_files = [
            make_file(id=8, path=media_file.path),
            make_file(id=11, path=media_file.path),
        ]
        repository = create_autospec(FileRepository, instance=True)
        repository.first_with_episodes.return_value = media_file
        repository.all_by_path.return_value = same_path_files
        use_file_repository(monkeypatch, repository)
        view = MediaFileAdminView()
        request = Request({"type": "http", "path_params": {"pk": "7"}})

        result = await view.get_object_for_edit(request)

        assert result is media_file
        assert getattr(media_file, "same_path_files") == same_path_files
        repository.first_with_episodes.assert_awaited_once_with(7)
        repository.all_by_path.assert_awaited_once_with(
            "audio/shared.mp3",
            excluded_ids=(7,),
        )

    async def test_get_object_for_edit__does_not_match_other_empty_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        media_file = make_file(id=7, path="")
        repository = create_autospec(FileRepository, instance=True)
        repository.first_with_episodes.return_value = media_file
        repository.all_by_path.return_value = []
        use_file_repository(monkeypatch, repository)
        view = MediaFileAdminView()

        result = await view.get_object_for_edit(
            Request({"type": "http", "path_params": {"pk": "7"}})
        )

        assert result is media_file
        assert getattr(media_file, "same_path_files") == []
        repository.all_by_path.assert_awaited_once_with("", excluded_ids=(7,))

    async def test_open_external_file__redirects_to_fresh_presigned_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        media_file = make_file(id=7, path="audio/seven.mp3")
        fetch_presigned_url = AsyncMock(
            return_value="https://s3.example.test/signed?signature=temporary"
        )
        monkeypatch.setattr(
            "src.modules.admin.views.media.get_file_presigned_url", fetch_presigned_url
        )
        repository = create_autospec(FileRepository, instance=True)
        repository.first.return_value = media_file
        use_file_repository(monkeypatch, repository)
        view = MediaFileAdminView()
        request = SimpleNamespace(query_params={"pks": "7"})

        action_handler = MediaFileAdminView.open_external_file.__wrapped__
        response = await action_handler(view, request)

        fetch_presigned_url.assert_awaited_once_with(media_file)
        repository.first.assert_awaited_once_with(7)
        assert response.status_code == 307
        assert response.headers["location"] == (
            "https://s3.example.test/signed?signature=temporary"
        )

    async def test_update_model__does_not_write_readonly_file_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        update_model = AsyncMock(return_value=make_file())
        monkeypatch.setattr(BaseModelView, "update_model", update_model)
        data = {"type": "audio", "size": "534104570", "available": True}

        await MediaFileAdminView().update_model(Request({"type": "http"}), "1", data)

        update_model.assert_awaited_once_with(
            ANY,
            "1",
            {"available": True},
        )

    async def test_clear_external_files_action__passes_all_ids_and_registers_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=1,
                    status=FileCleanupStatus.CLEARED,
                    path="audio/one.mp3",
                ),
                FileCleanupItemResult(
                    file_id=2,
                    status=FileCleanupStatus.FAILED,
                    path="audio/two.mp3",
                    error="S3 unavailable",
                ),
            )
        )
        clear_files = AsyncMock(return_value=result)
        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=clear_files),
        )
        alert_context_var.set(None)
        request = SimpleNamespace(
            query_params={"pks": "1,2"},
            url_for=lambda *args, **kwargs: URL("https://admin.test/padm/file/list"),
            session={},
        )

        action_handler = MediaFileAdminView.clear_external_files.__wrapped__
        response = await action_handler(MediaFileAdminView(), request)

        clear_files.assert_awaited_once_with([1, 2])
        assert response.status_code == 302
        alert_context_var.set(None)
        assert get_current_error_alert(request) == {
            "title": "External file cleanup completed with errors",
            "details": "File #2 (audio/two.mp3): S3 unavailable",
            "level": "error",
        }
        assert get_current_error_alert(request) is None

    async def test_on_model_delete__allows_delete_after_successful_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=1,
                    status=FileCleanupStatus.CLEARED,
                    path="audio/one.mp3",
                ),
            )
        )
        clear_files = AsyncMock(return_value=result)
        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=clear_files),
        )

        await MediaFileAdminView().on_model_delete(
            make_file(id=1),
            Request({"type": "http"}),
        )

        clear_files.assert_awaited_once_with([1])

    async def test_clear_external_files_action__returns_to_edit_for_single_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=7,
                    status=FileCleanupStatus.CLEARED,
                    path="audio/seven.mp3",
                ),
            )
        )
        clear_files = AsyncMock(return_value=result)
        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=clear_files),
        )
        requested_routes: list[tuple[str, dict[str, object]]] = []

        def url_for(name: str, **params: object) -> URL:
            requested_routes.append((name, params))
            return URL("https://admin.test/padm/file/edit/7")

        request = SimpleNamespace(
            query_params={"pks": "7", "return_to": "edit"},
            url_for=url_for,
            session={},
        )

        action_handler = MediaFileAdminView.clear_external_files.__wrapped__
        response = await action_handler(MediaFileAdminView(), request)

        clear_files.assert_awaited_once_with([7])
        assert response.status_code == 302
        assert response.headers["location"] == "https://admin.test/padm/file/edit/7"
        assert requested_routes == [("admin:edit", {"identity": "file", "pk": 7})]
        alert_context_var.set(None)
        assert get_current_error_alert(request) == {
            "title": "External file cleanup completed",
            "details": "Cleared File IDs: [7]",
            "level": "success",
        }

    async def test_clear_external_files_action__shows_success_for_already_clear_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=104,
                    status=FileCleanupStatus.ALREADY_CLEAR,
                ),
            )
        )
        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=AsyncMock(return_value=result)),
        )
        request = SimpleNamespace(
            query_params={"pks": "104", "return_to": "edit"},
            url_for=lambda *args, **kwargs: URL("https://admin.test/padm/file/edit/104"),
            session={},
        )

        action_handler = MediaFileAdminView.clear_external_files.__wrapped__
        await action_handler(MediaFileAdminView(), request)

        alert_context_var.set(None)
        assert get_current_error_alert(request) == {
            "title": "External file cleanup completed",
            "details": "Already clear File IDs: [104]",
            "level": "success",
        }

    async def test_on_model_delete__raises_and_registers_alert_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=1,
                    status=FileCleanupStatus.FAILED,
                    path="audio/one.mp3",
                    error="shared path",
                ),
            )
        )
        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=AsyncMock(return_value=result)),
        )
        alert_context_var.set(None)

        with pytest.raises(RuntimeError, match="shared path"):
            await MediaFileAdminView().on_model_delete(
                make_file(id=1),
                Request({"type": "http", "session": {}}),
            )

        assert get_current_error_alert() == {
            "title": "File deletion failed",
            "details": "File #1 (audio/one.mp3): shared path",
            "level": "error",
        }

    async def test_delete_model__cleans_storage_before_database_delete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []
        model = make_file(id=1)
        result = FileCleanupBatchResult(
            items=(
                FileCleanupItemResult(
                    file_id=1,
                    status=FileCleanupStatus.CLEARED,
                    path=model.path,
                ),
            )
        )

        async def clear_files(_: list[int]) -> FileCleanupBatchResult:
            events.append("s3_cleanup")
            return result

        monkeypatch.setattr(
            "src.modules.admin.views.media.FileStorageCleanupService",
            lambda: SimpleNamespace(clear_files=clear_files),
        )

        class DeleteQueryResult:
            def scalars(self) -> "DeleteQueryResult":
                return self

            def first(self):
                return model

        class DeleteSession:
            async def __aenter__(self) -> "DeleteSession":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def execute(self, *_: object) -> DeleteQueryResult:
                return DeleteQueryResult()

            async def delete(self, _: object) -> None:
                events.append("db_delete")

            async def commit(self) -> None:
                events.append("db_commit")

        view = MediaFileAdminView()
        view.session_maker = lambda: DeleteSession()

        request = Request({"type": "http", "session": {}})
        await view.delete_model(request, "1")

        assert events == ["s3_cleanup", "db_delete", "db_commit"]
        alert_context_var.set(None)
        assert get_current_error_alert(request) == {
            "title": "File deleted",
            "details": "File #1 was deleted after external storage cleanup.",
            "level": "success",
        }

    def test_clear_external_files_action__is_registered_for_list_with_confirmation(self) -> None:
        action_handler = MediaFileAdminView.clear_external_files

        assert action_handler._action is True
        assert action_handler._slug == "clear-external-files"
        assert action_handler._add_in_list is True
        assert action_handler._add_in_detail is False
        assert action_handler._confirmation_message
