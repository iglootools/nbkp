"""Disks setup-auth command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from ...config.cli.helpers import load_config_or_exit
from ..auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH, generate_auth_rules
from . import app


@app.command("setup-auth")
def setup_auth(
    config: Annotated[
        Optional[Path],
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
        str,
        typer.Option("--user", "-u", help="System user for auth rules"),
    ] = "ubuntu",
) -> None:
    """Generate polkit and sudoers configuration for mount management."""
    cfg = load_config_or_exit(config)
    rules = generate_auth_rules(cfg, user)

    if rules.polkit is None and rules.sudoers is None:
        typer.echo("No volumes with mount config found.", err=True)
        raise typer.Exit(0)

    if rules.polkit is not None:
        typer.echo("# polkit rules")
        typer.echo(f"# Install to: {POLKIT_RULES_PATH}")
        typer.echo()
        typer.echo(rules.polkit)

    if rules.sudoers is not None:
        typer.echo("# sudoers rules")
        typer.echo(f"# Install with: sudo visudo -f {SUDOERS_RULES_PATH}")
        typer.echo()
        typer.echo(rules.sudoers)
