import pytest

from src.exceptions import ImproperlyConfiguredError
from src.modules.services.encryption import EncodingError, SensitiveData
from src.settings.app import AppSettings


class TestSensitiveData:
    def test_encrypt_decrypt__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = AppSettings(sens_data_encrypt_key="x" * SensitiveData.AES_KEY_LENGTH)
        monkeypatch.setattr("src.modules.services.encryption.get_app_settings", lambda: settings)
        service = SensitiveData()

        encrypted = service.encrypt("secret")

        assert encrypted.startswith("AES256;")
        assert service.decrypt(encrypted) == "secret"

    def test_init__missing_key__fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = AppSettings(sens_data_encrypt_key=None)
        monkeypatch.setattr("src.modules.services.encryption.get_app_settings", lambda: settings)

        with pytest.raises(ImproperlyConfiguredError):
            SensitiveData()

    @pytest.mark.parametrize(
        ("encoded_data", "error"),
        [
            ("bad", "not enough parts"),
            ("AES128;bm9uY2U=;bXNn;dGFn", "unexpected prefix"),
        ],
    )
    def test_get_struct_message__bad_data__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        encoded_data: str,
        error: str,
    ) -> None:
        settings = AppSettings(sens_data_encrypt_key="x" * SensitiveData.AES_KEY_LENGTH)
        monkeypatch.setattr("src.modules.services.encryption.get_app_settings", lambda: settings)
        service = SensitiveData()

        with pytest.raises(ValueError, match=error):
            service._get_struct_message(encoded_data)

    def test_decrypt__tampered_data__fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = AppSettings(sens_data_encrypt_key="x" * SensitiveData.AES_KEY_LENGTH)
        monkeypatch.setattr("src.modules.services.encryption.get_app_settings", lambda: settings)
        service = SensitiveData()
        encrypted = service.encrypt("secret")
        tampered = encrypted.rsplit(";", maxsplit=1)[0] + ";dGFtcGVyZWQ="

        with pytest.raises(EncodingError):
            service.decrypt(tampered)

    @pytest.mark.parametrize(
        ("raw_key", "expected"),
        [
            ("x" * 32, b"x" * 32),
            ("y" * 40, b"y" * 32),
        ],
    )
    def test_cast_encrypt_key__ok(self, raw_key: str, expected: bytes) -> None:
        assert SensitiveData._cast_encrypt_key(raw_key) == expected

    def test_cast_encrypt_key__too_short__fail(self) -> None:
        with pytest.raises(ValueError, match="Not enough length encryption key"):
            SensitiveData._cast_encrypt_key("short")
