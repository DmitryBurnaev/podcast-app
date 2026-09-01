from wtforms import Form

from src.modules.admin.forms import ReadOnlyIntegerField, ReadOnlyTextField


class ReadOnlyTextForm(Form):
    value = ReadOnlyTextField()


class ReadOnlyIntegerForm(Form):
    value = ReadOnlyIntegerField()


class TestReadOnlyTextField:
    def test_renders_readonly_without_disabling_submission(self) -> None:
        field = ReadOnlyTextForm(value="audio").value

        rendered = str(field())

        assert "readonly" in rendered
        assert "disabled" not in rendered

    def test_preserves_integer_values(self) -> None:
        field = ReadOnlyIntegerForm(value="534104570").value

        assert field.data == 534104570
