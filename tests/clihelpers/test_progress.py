"""Tests for StepProgressBar result lines."""

from __future__ import annotations

import pytest

from nbkp.clihelpers import Severity, StepProgressBar

# An error message with square brackets in it.  Rich parses "[...]" in a
# markup string as a style tag, so a bar that formatted its result line as
# markup would silently drop this — which is exactly how the keyring hint
# used to render as "pip install nbkp".
BRACKETED = "keyring missing. Install with: uv tool install 'nbkp[keyring]'"


def _run(
    capsys: pytest.CaptureFixture[str],
    severity: Severity,
    detail: str | None,
) -> str:
    """Run one start/end cycle and return what was printed."""
    bar = StepProgressBar(1)
    bar.on_start("mount my-vol")
    bar.on_end("mount my-vol", severity, detail)
    bar.stop()
    return capsys.readouterr().out


class TestStepProgressBarResultLine:
    def test_detail_brackets_survive(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert "nbkp[keyring]" in _run(capsys, Severity.WARNING, BRACKETED)

    def test_label_and_symbol_rendered(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert "✓ mount my-vol" in _run(capsys, Severity.OK, None)

    def test_no_detail_omits_parentheses(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert "(" not in _run(capsys, Severity.OK, None)
