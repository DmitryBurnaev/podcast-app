from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from starlette.datastructures import URL
from starlette.requests import Request

from src.modules.admin.views import MediaFileAdminView
from src.modules.admin.views.base import BaseModelView
from src.modules.admin.utils import alert_context_var, get_current_error_alert
from src.modules.services.storage import (
    FileCleanupBatchResult,
    FileCleanupItemResult,
    FileCleanupStatus,
)
from src.tests.factories import make_episode, make_file


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
        assert MediaFileAdminView.edit_template == "media_edit.html"

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
        )

        action_handler = MediaFileAdminView.clear_external_files.__wrapped__
        response = await action_handler(MediaFileAdminView(), request)

        clear_files.assert_awaited_once_with([1, 2])
        assert response.status_code == 302
        assert get_current_error_alert() == {
            "title": "External file cleanup completed with errors",
            "details": "File #2 (audio/two.mp3): S3 unavailable",
        }

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
                Request({"type": "http"}),
            )

        assert get_current_error_alert() == {
            "title": "File deletion failed",
            "details": "File #1 (audio/one.mp3): shared path",
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

        await view.delete_model(Request({"type": "http"}), "1")

        assert events == ["s3_cleanup", "db_delete", "db_commit"]

    def test_clear_external_files_action__is_registered_for_list_with_confirmation(self) -> None:
        action_handler = MediaFileAdminView.clear_external_files

        assert action_handler._action is True
        assert action_handler._slug == "clear-external-files"
        assert action_handler._add_in_list is True
        assert action_handler._add_in_detail is False
        assert action_handler._confirmation_message
