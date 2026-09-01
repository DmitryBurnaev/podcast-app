from unittest.mock import ANY, AsyncMock

import pytest
from starlette.requests import Request

from src.modules.admin.views import MediaFileAdminView
from src.modules.admin.views.base import BaseModelView
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
