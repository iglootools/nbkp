"""Tests for the CLI passphrase prompt."""

import typer
from typer.testing import CliRunner

from nbkp.cliwiring.credentials import prompt_passphrase

_app = typer.Typer()


@_app.command()
def ask_passphrase() -> None:
    typer.echo(f"got {prompt_passphrase('disk1')}")


class TestPromptPassphrase:
    def test_prompts_with_hidden_input(self) -> None:
        result = CliRunner().invoke(_app, input="typed-secret\n")

        assert result.exit_code == 0, result.output
        assert result.output.startswith("LUKS passphrase for disk1: ")
        assert "got typed-secret" in result.output
        # A visible prompt would echo the typed input as well.
        assert result.output.count("typed-secret") == 1
