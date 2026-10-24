"""Secret setup must preserve configured credentials and avoid disclosing values."""

import re
from pathlib import Path

import pytest

from src.cli.generate_secrets import SECRET_KEYS, generate_secrets, main


class TestGenerateSecrets:
    def test_fills_missing_values_and_preserves_other_configuration(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        path.write_text(
            "# configuration\nDB_HOST=external-db\nAPP_SECRET_KEY=\nADMIN_PASSWORD='kept value'\n",
            encoding="utf-8",
        )
        generated = generate_secrets(path)
        content = path.read_text(encoding="utf-8")
        assert generated == [key for key in SECRET_KEYS if key != "ADMIN_PASSWORD"]
        assert "# configuration\nDB_HOST=external-db\n" in content
        assert "ADMIN_PASSWORD='kept value'\n" in content
        for key in generated:
            assert re.search(rf"^{key}=[A-Za-z0-9_-]{{64}}$", content, re.MULTILINE)
        assert path.stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("value", ["", "   ", "''", '"" # fill this', " # fill this"])
    def test_fills_blank_assignments(self, tmp_path: Path, value: str) -> None:
        path = tmp_path / ".env"
        path.write_text(f"export APP_SECRET_KEY={value}\n", encoding="utf-8")
        assert "APP_SECRET_KEY" in generate_secrets(path)
        assert re.search(r"^export APP_SECRET_KEY=[A-Za-z0-9_-]{64}$", path.read_text(), re.M)

    @pytest.mark.parametrize("value", ["#literal-password", "'$(do-not-execute)'", '"existing"'])
    def test_preserves_nonempty_values_and_repeat_runs(self, tmp_path: Path, value: str) -> None:
        path = tmp_path / ".env"
        path.write_text(f"APP_SECRET_KEY={value}\n", encoding="utf-8")
        generate_secrets(path)
        original = path.read_bytes()
        assert generate_secrets(path) == []
        assert path.read_bytes() == original
        assert f"APP_SECRET_KEY={value}\n" in path.read_text()

    def test_rejects_duplicates_before_changing_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        original = b"DB_PASSWORD=keep-me\nDB_PASSWORD=\n"
        path.write_bytes(original)
        with pytest.raises(ValueError, match="Duplicate DB_PASSWORD"):
            generate_secrets(path)
        assert path.read_bytes() == original

    def test_cli_does_not_print_secret_values(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / ".env"
        path.touch()
        assert main(["--env-file", str(path)]) == 0
        output = capsys.readouterr()
        for line in path.read_text().splitlines():
            key, value = line.split("=", 1)
            assert key in output.out
            assert value not in output.out + output.err

    def test_cli_reports_a_missing_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--env-file", str(tmp_path / "missing")]) == 1
        assert "Cannot prepare secrets" in capsys.readouterr().err
