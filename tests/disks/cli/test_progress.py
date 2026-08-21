"""Tests for the mount/umount result-line formatters."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from nbkp.clihelpers import Severity
from nbkp.disks.cli.helpers import (
    _error_label,
    format_mount_result,
    format_umount_result,
)

# Real-world details that contain square brackets.  Rich reads "[...]" in a
# markup string as a style tag, so these are the strings a markup-formatted
# line would silently truncate.
KEYRING_HINT = "unreachable: keyring missing. Install: uv tool install 'nbkp[keyring]'"
RSYNC_ERROR = "rsync error: code 23 at main.c(1338) [sender=3.4.1]"


def _render(renderable: object) -> str:
    buf = StringIO()
    Console(file=buf, width=200).print(renderable)
    return buf.getvalue()


class TestFormatMountResult:
    def test_detail_brackets_survive(self) -> None:
        line = _render(format_mount_result("vol", Severity.WARNING, KEYRING_HINT, None))
        assert "nbkp[keyring]" in line

    def test_symbol_and_slug(self) -> None:
        line = _render(format_mount_result("vol", Severity.OK, None, None))
        assert line.strip() == "✓ mount vol"


class TestFormatUmountResult:
    def test_detail_brackets_survive(self) -> None:
        line = _render(format_umount_result("vol", Severity.ERROR, RSYNC_ERROR, None))
        assert "[sender=3.4.1]" in line

    def test_warning_brackets_survive(self) -> None:
        line = _render(
            format_umount_result("vol", Severity.ERROR, None, "still held [see lsblk]")
        )
        assert "warning: still held [see lsblk]" in line

    def test_symbol_and_slug(self) -> None:
        line = _render(format_umount_result("vol", Severity.OK, None, None))
        assert line.strip() == "✓ umount vol"


class TestErrorLabel:
    def test_detail_brackets_survive(self) -> None:
        assert "nbkp[keyring]" in _render(_error_label("my-vol", KEYRING_HINT))

    def test_plain_text_carries_no_markup(self) -> None:
        # The same label is reused as the JSON `volume` field, so its plain
        # form must be free of both markup and escapes.
        assert _error_label("my-vol", KEYRING_HINT).plain == f"my-vol ✗ {KEYRING_HINT}"

    def test_no_detail_is_bare_name(self) -> None:
        assert _error_label("my-vol", None).plain == "my-vol"
