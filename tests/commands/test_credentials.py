"""Tests for the CLI passphrase prompt."""

from __future__ import annotations

from unittest.mock import patch

from nbkp.commands.credentials import prompt_passphrase


class TestPromptPassphrase:
    def test_prompts_with_hidden_input(self) -> None:
        with patch("nbkp.commands.credentials.typer") as mock_typer:
            mock_typer.prompt.return_value = "typed-secret"
            result = prompt_passphrase("disk1")
        assert result == "typed-secret"
        mock_typer.prompt.assert_called_once_with(
            "LUKS passphrase for disk1",
            hide_input=True,
        )
