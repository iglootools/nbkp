"""Disks setup-auth command."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Annotated

import typer

from ...clihelpers import OutputFormat, echo_json
from ...config.cli.helpers import load_config_or_exit
from ..auth import AuthRuleBlock, generate_auth_rules
from . import app


@app.command("setup-auth")
def setup_auth(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            "-u",
            help=(
                "System user the rule authorizes: the user nbkp runs as on the"
                " host (the SSH user for remote volumes)."
                " Defaults to the current user."
            ),
        ),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
) -> None:
    """Generate the polkit rule for udisks-based mount management."""
    cfg = load_config_or_exit(config, output)
    rules = generate_auth_rules(cfg, user if user is not None else getpass.getuser())
    blocks = list(rules.blocks())
    match output:
        case OutputFormat.JSON:
            echo_json({"rules": [_block_json(block) for block in blocks]})
        case OutputFormat.HUMAN:
            _print_blocks(blocks)


def _block_json(block: AuthRuleBlock) -> dict[str, str]:
    return {"name": block.name, "path": block.path, "content": block.content}


def _print_blocks(blocks: list[AuthRuleBlock]) -> None:
    if not blocks:
        typer.echo("No volumes with mount config found.", err=True)
    for block in blocks:
        typer.echo(f"# {block.name}")
        typer.echo(f"# {block.install_hint}")
        typer.echo()
        typer.echo(block.content)
