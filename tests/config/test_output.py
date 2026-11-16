"""Tests for config error display."""

from io import StringIO

import pytest
from pydantic import BaseModel, ValidationError
from rich.console import Console

from nbkp.config import ConfigError, ConfigErrorReason
from nbkp.config.output import config_error_json, print_config_error


def _render(e: ConfigError) -> str:
    buf = StringIO()
    print_config_error(e, console=Console(file=buf, width=200))
    return buf.getvalue()


class TestPrintConfigError:
    def test_reason_survives_in_title(self) -> None:
        """The title's own brackets are literal, not a Rich style tag."""
        e = ConfigError("boom", reason=ConfigErrorReason.INVALID_YAML)
        assert f"[{ConfigErrorReason.INVALID_YAML}]" in _render(e)

    def test_message_brackets_survive(self) -> None:
        """YAML parser messages quote the offending text, brackets included."""
        e = ConfigError(
            "Invalid YAML in /etc/nbkp.yaml: found unexpected ']' at line 3",
            reason=ConfigErrorReason.INVALID_YAML,
        )
        assert "unexpected ']'" in _render(e)

    def test_validation_error_details_rendered(self) -> None:
        """Each problem is listed with its location and its message."""
        e = _validation_error()
        assert "retention_count: Input should be a valid integer" in _render(e)

    def test_validation_error_details_in_json(self) -> None:
        [error] = config_error_json(_validation_error())["error"]["errors"]
        assert error["loc"] == ["retention_count"]
        assert error["type"] == "int_parsing"


def _validation_error() -> ConfigError:
    class M(BaseModel):
        retention_count: int

    with pytest.raises(ValidationError) as exc:
        M(retention_count="x")  # type: ignore[arg-type]
    e = ConfigError("bad config", reason=ConfigErrorReason.VALIDATION)
    e.__cause__ = exc.value
    return e
