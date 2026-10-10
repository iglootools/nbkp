"""Disks status command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ...clihelpers import OutputFormat
from ...commands.config import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType
from . import app
from .helpers import _probe_and_show_status, require_known_names


@app.command("status")
def status(
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
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    name: Annotated[
        list[str] | None,
        typer.Option("--name", "-n", help="Volume name(s) to check"),
    ] = None,
    location: Annotated[
        list[str] | None,
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        NetworkType | None,
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
) -> None:
    """Show mount status for volumes with mount config."""
    cfg = load_config_or_exit(config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    _probe_and_show_status(cfg, resolved, output, name)
