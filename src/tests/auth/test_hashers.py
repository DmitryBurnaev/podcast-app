from unittest.mock import Mock

import pytest

from src.modules.auth.hashers import PBKDF2PasswordHasher, get_random_hash, get_salt


class TestPasswordHashHelpers:
    def test_get_salt__requested_length(self) -> None:
        salt = get_salt(length=24)

        assert len(salt) == 24
        assert salt.isalnum()

    def test_get_random_hash__requested_size(self) -> None:
        hash_value = get_random_hash(size=16)

        assert len(hash_value) == 16


class TestPBKDF2PasswordHasher:
    def test_encode__uses_generated_salt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.modules.auth.hashers.get_salt", Mock(return_value="static-salt"))

        encoded = PBKDF2PasswordHasher().encode("secret")

        assert encoded.split("$")[:3] == ["pbkdf2_sha256", "180000", "static-salt"]

    def test_verify__valid_password__ok(self) -> None:
        hasher = PBKDF2PasswordHasher()
        encoded = hasher.encode("secret", salt="static-salt")

        verified, reason = hasher.verify("secret", encoded)

        assert verified is True
        assert reason == ""

    def test_verify__wrong_password__fail(self) -> None:
        hasher = PBKDF2PasswordHasher()
        encoded = hasher.encode("secret", salt="static-salt")

        verified, reason = hasher.verify("another", encoded)

        assert verified is False
        assert reason == ""

    @pytest.mark.parametrize(
        ("encoded", "reason"),
        [
            ("bad-format", "not enough values to unpack"),
            ("pbkdf2_sha256$180000$bad$salt$hash", "Extra parts detected"),
        ],
    )
    def test_verify__bad_format__fail(self, encoded: str, reason: str) -> None:
        verified, message = PBKDF2PasswordHasher().verify("secret", encoded)

        assert verified is False
        assert reason in message

    def test_verify__unexpected_algorithm__fail(self) -> None:
        encoded = "argon2$180000$static-salt$hash"

        verified, reason = PBKDF2PasswordHasher().verify("secret", encoded)

        assert verified is False
        assert reason == "Algorithm mismatch"

    @pytest.mark.parametrize(
        ("password", "salt", "error"),
        [
            ("", "salt", "Password is required"),
            ("secret", "", "Salt is required"),
            ("secret", "bad$salt", "Salt has incompatible format"),
        ],
    )
    def test_validate_input__invalid__fail(
        self,
        password: str,
        salt: str,
        error: str,
    ) -> None:
        with pytest.raises(ValueError, match=error):
            PBKDF2PasswordHasher._validate_input(password, salt)
