import asyncio
import logging
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from src.exceptions import NotFoundError
from src.utils import (
    create_task,
    cut_string,
    download_content,
    hash_string,
    is_basic_emoji,
    log_message,
    send_email,
    simple_slugify,
    singleton,
    utcnow,
)


class TestSmallUtils:
    def test_singleton__returns_same_instance(self) -> None:
        @singleton
        class Service:
            def __init__(self, value: int) -> None:
                self.value = value

        first = Service(1)
        second = Service(2)

        assert first is second
        assert second.value == 1

    def test_utcnow__timezone_modes(self) -> None:
        assert utcnow().tzinfo is None
        assert utcnow(skip_tz=False).tzinfo is not None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (" Hello World ", "hello-world"),
            ("already-slug", "already-slug"),
        ],
    )
    def test_simple_slugify__ok(self, value: str, expected: str) -> None:
        assert simple_slugify(value) == expected

    @pytest.mark.parametrize(
        ("value", "max_length", "placeholder", "expected"),
        [
            (None, 5, "...", ""),
            ("hello", 5, "...", "hello"),
            ("hello world", 5, "...", "hello..."),
            ("hello world", 5, "", "hello"),
        ],
    )
    def test_cut_string__ok(
        self,
        value: str | None,
        max_length: int,
        placeholder: str,
        expected: str,
    ) -> None:
        assert cut_string(value, max_length=max_length, placeholder=placeholder) == expected

    @pytest.mark.parametrize(("char", "expected"), [("★", True), ("a", False)])
    def test_is_basic_emoji__ok(self, char: str, expected: bool) -> None:
        assert is_basic_emoji(char) is expected

    def test_hash_string__stable_sha256(self) -> None:
        assert hash_string("127.0.0.1") == (
            "12ca17b49af2289436f303e0166030a21e525d266e209267433801a8fd4071a0"
        )

    def test_log_message__ok(self, caplog: pytest.LogCaptureFixture) -> None:
        exception = RuntimeError("boom")

        with caplog.at_level(logging.WARNING):
            log_message(
                exception, {"error": "Failure", "details": "details"}, level=logging.WARNING
            )

        assert "RuntimeError 'Failure': [details]" in caplog.text

    async def test_send_email__builds_html_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        smtp_client = _FakeSMTPClient()
        monkeypatch.setattr("src.utils.aiosmtplib.SMTP", Mock(return_value=smtp_client))
        monkeypatch.setattr(
            "src.utils.get_app_settings",
            lambda: SimpleNamespace(
                smtp=SimpleNamespace(
                    host="smtp.example.com",
                    port=465,
                    username="user",
                    password=SecretStr("password"),
                    from_email="podcast@example.com",
                    use_tls=True,
                    starttls=None,
                )
            ),
        )

        await send_email("listener@example.com", "Hello", "<p>Welcome</p>")

        message = smtp_client.send_message.await_args.args[0]
        assert message["From"] == "podcast@example.com"
        assert message["To"] == "listener@example.com"
        assert message["Subject"] == "Hello"
        assert "<p>Welcome</p>" in message.get_payload()[0].get_payload()


class TestDownloadContent:
    async def test_download_content__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        client = _FakeAsyncClient([SimpleNamespace(status_code=200, content=b"data", text="OK")])
        monkeypatch.setattr("src.utils.httpx.AsyncClient", Mock(return_value=client))
        monkeypatch.setattr(
            "src.utils.get_app_settings",
            lambda: SimpleNamespace(http_proxy_url="http://proxy", tmp_path=tmp_path),
        )

        result = await download_content("https://example.com/file", file_ext="jpg")

        assert result is not None
        assert result.suffix == ".jpg"
        assert result.read_bytes() == b"data"
        client.get.assert_awaited_once_with("https://example.com/file", timeout=600)

    async def test_download_content__not_found__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        client = _FakeAsyncClient(
            [SimpleNamespace(status_code=HTTPStatus.NOT_FOUND, content=b"", text="")]
        )
        monkeypatch.setattr("src.utils.httpx.AsyncClient", Mock(return_value=client))
        monkeypatch.setattr(
            "src.utils.get_app_settings",
            lambda: SimpleNamespace(http_proxy_url=None, tmp_path=tmp_path),
        )

        with pytest.raises(NotFoundError, match="Resource not found"):
            await download_content("https://example.com/missing", file_ext="jpg", retries=1)

    async def test_download_content__retries_then_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        client = _FakeAsyncClient(
            [
                RuntimeError("network"),
                SimpleNamespace(status_code=503, content=b"", text="busy"),
                SimpleNamespace(status_code=200, content=b"data", text="OK"),
            ]
        )
        sleep = AsyncMock()
        monkeypatch.setattr("src.utils.httpx.AsyncClient", Mock(return_value=client))
        monkeypatch.setattr("src.utils.asyncio.sleep", sleep)
        monkeypatch.setattr(
            "src.utils.get_app_settings",
            lambda: SimpleNamespace(http_proxy_url=None, tmp_path=tmp_path),
        )

        result = await download_content("https://example.com/file", file_ext="txt", retries=3)

        assert result is not None
        assert result.read_bytes() == b"data"
        assert sleep.await_count == 2

    async def test_download_content__exhausted__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        client = _FakeAsyncClient([RuntimeError("network"), RuntimeError("network")])
        monkeypatch.setattr("src.utils.httpx.AsyncClient", Mock(return_value=client))
        monkeypatch.setattr("src.utils.asyncio.sleep", AsyncMock())
        monkeypatch.setattr(
            "src.utils.get_app_settings",
            lambda: SimpleNamespace(http_proxy_url=None, tmp_path=tmp_path),
        )

        with pytest.raises(NotFoundError, match="Couldn't download url"):
            await download_content("https://example.com/file", file_ext="txt", retries=2)


class TestCreateTask:
    async def test_create_task__returns_task_result(self) -> None:
        async def work() -> str:
            return "done"

        task = create_task(work(), log_instance=Mock())

        assert await task == "done"

    async def test_create_task__logs_exception(self) -> None:
        async def fail() -> None:
            raise RuntimeError("boom")

        logger = Mock()
        task = create_task(
            fail(),
            log_instance=logger,
            error_message="failed %s",
            error_message_message_args=("x",),
        )

        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

        logger.exception.assert_called_once_with("failed %s", "x")

    async def test_create_task__cancelled_is_not_logged(self) -> None:
        async def wait() -> None:
            await asyncio.sleep(10)

        logger = Mock()
        task = create_task(wait(), log_instance=logger)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        logger.exception.assert_not_called()


class _FakeAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.get = AsyncMock(side_effect=responses)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSMTPClient:
    def __init__(self) -> None:
        self.send_message = AsyncMock(return_value=({}, "OK"))

    async def __aenter__(self) -> "_FakeSMTPClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None
