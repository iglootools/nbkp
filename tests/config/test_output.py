"""Tests for config error display."""

from io import StringIO

import pytest
from pydantic import BaseModel, ValidationError
from rich.console import Console

from nbkp.config import ConfigError, ConfigErrorKind
from nbkp.config.output import config_error_json, format_config_error


def _render(e: ConfigError) -> str:
    buf = StringIO()
    Console(file=buf, width=200).print(format_config_error(e))
    return buf.getvalue()


class TestFormatConfigError:
    def test_reason_survives_in_title(self) -> None:
        """The title's own brackets are literal, not a Rich style tag."""
        e = ConfigError(ConfigErrorKind.INVALID_YAML, detail="boom")
        assert f"[{ConfigErrorKind.INVALID_YAML}]" in _render(e)

    def test_message_brackets_survive(self) -> None:
        """YAML parser messages quote the offending text, brackets included."""
        e = ConfigError(
            ConfigErrorKind.INVALID_YAML,
            path="/etc/nbkp.yaml",
            detail="found unexpected ']' at line 3",
        )
        assert "unexpected ']'" in _render(e)

    def test_validation_error_details_rendered(self) -> None:
        """Each problem is listed with its location and its message."""
        e = _validation_error()
        assert "retention_count: Input should be a valid integer" in _render(e)

    def test_validation_error_names_config_file(self) -> None:
        """The search covers several locations: the panel says which file failed."""
        assert "Invalid config /etc/nbkp/config.yaml:" in _render(_validation_error())

    def test_validation_error_details_in_json(self) -> None:
        [error] = config_error_json(_validation_error())["error"]["errors"]
        assert error["loc"] == ["retention_count"]
        assert error["type"] == "int_parsing"


def _validation_error() -> ConfigError:
    class M(BaseModel):
        retention_count: int  # noqa: DC06 -- the field the error names

    with pytest.raises(ValidationError) as exc:
        M.model_validate({"retention_count": "x"})
    e = ConfigError(
        ConfigErrorKind.VALIDATION,
        path="/etc/nbkp/config.yaml",
        detail="bad config",
    )
    e.__cause__ = exc.value
    return e
