"""Disks status command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat
from ...clihelpers import StepProgressBar
from ...config.clihelpers import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType
from . import app
from .helpers import _probe_volume_status, _show_status_table, _unmanaged_statuses


@app.command("status")
def volumes_status(
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
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    name: Annotated[
        Optional[list[str]],
        typer.Option("--name", "-n", help="Volume name(s) to check"),
    ] = None,
    location: Annotated[
        Optional[list[str]],
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        Optional[NetworkType],
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
) -> None:
    """Show mount status for volumes with mount config."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    managed = [
        (slug, vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (name is None or slug in name)
    ]

    bar = (
        StepProgressBar(len(managed))
        if output == OutputFormat.HUMAN and managed
        else None
    )
    managed_statuses = [
        _probe_volume_status(vol, resolved, bar) for _slug, vol in managed
    ]
    if bar is not None:
        bar.stop()

    _show_status_table(
        [*managed_statuses, *_unmanaged_statuses(cfg, name)],
        output,
    )
