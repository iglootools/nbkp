"""Tests for the ``disks status`` error label."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from nbkp.disks.cli.helpers import _error_label

# Real-world details that contain square brackets.  Rich reads "[...]" in a
# markup string as a style tag, so these are the strings a markup-formatted
# line would silently truncate.
KEYRING_HINT = "unreachable: keyring missing. Install: uv tool install 'nbkp[keyring]'"


def _render(renderable: object) -> str:
    buf = StringIO()
    Console(file=buf, width=200).print(renderable)
    return buf.getvalue()


class TestErrorLabel:
    def test_detail_brackets_survive(self) -> None:
        assert "nbkp[keyring]" in _render(_error_label("my-vol", KEYRING_HINT))

    def test_plain_text_carries_no_markup(self) -> None:
        # The same label is reused as the JSON `volume` field, so its plain
        # form must be free of both markup and escapes.
        assert _error_label("my-vol", KEYRING_HINT).plain == f"my-vol ✗ {KEYRING_HINT}"

    def test_no_detail_is_bare_name(self) -> None:
        assert _error_label("my-vol", None).plain == "my-vol"
