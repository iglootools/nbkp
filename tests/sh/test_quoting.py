"""Tests for nbkp.sh.quoting."""

from __future__ import annotations

import subprocess

from nbkp.sh.quoting import SCRIPT_DIR, SNAPSHOT_TS, comment_out, quote, shell_var


def _echo(word: str, env: dict[str, str] | None = None) -> str:
    """What bash produces for *word* (quoted shell text)."""
    result = subprocess.run(
        ["bash", "-c", f"printf %s {word}"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout


class TestQuote:
    def test_plain_path_single_quoted(self) -> None:
        assert quote("/mnt/my data") == "'/mnt/my data'"

    def test_literal_dollar_not_expanded(self) -> None:
        assert _echo(quote("/mnt/$HOME/x")) == "/mnt/$HOME/x"

    def test_placeholder_rendered_as_expansion(self) -> None:
        assert quote(f"/mnt/dst/snapshots/{SNAPSHOT_TS}/") == (
            '"/mnt/dst/snapshots/${NBKP_TS}/"'
        )

    def test_placeholder_with_special_characters_escaped(self) -> None:
        path = f'{SCRIPT_DIR}/a "b" $c `d` \\e'
        word = quote(path)
        assert _echo(word, env={"NBKP_SCRIPT_DIR": "/root"}) == (
            '/root/a "b" $c `d` \\e'
        )

    def test_shell_var(self) -> None:
        assert quote(shell_var("snap")) == '"${snap}"'


class TestCommentOut:
    def test_prefixes_non_blank_lines(self) -> None:
        assert comment_out("a\n\n  b\n") == "# a\n\n#   b\n"
