"""Tests for config error display."""

from __future__ import annotations

from io import StringIO

import pytest
from pydantic import BaseModel, ValidationError
from rich.console import Console

from nbkp.config import ConfigError, ConfigErrorReason
from nbkp.config.output import print_config_error


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
        class M(BaseModel):
            n: int

        with pytest.raises(ValidationError) as exc:
            M(n="x")  # type: ignore[arg-type]
        e = ConfigError("bad config", reason=ConfigErrorReason.VALIDATION)
        e.__cause__ = exc.value
        assert "n" in _render(e)
