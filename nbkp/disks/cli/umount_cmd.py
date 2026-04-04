"""Disks umount command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat
from .progress import DisksProgressBar
from ...config.clihelpers import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType
from ..lifecycle import UmountResult, mount_volume_count, umount_volumes
from ..output import volume_display_name
from . import app
from .helpers import _format_umount_result, _probe_and_show_status


@app.command("umount")
def volumes_umount(
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
        typer.Option("--name", "-n", help="Volume name(s) to umount"),
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
    """Umount volumes and close LUKS. Umounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    use_progress = output == OutputFormat.HUMAN
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }
    total = mount_volume_count(cfg, name)
    umount_bar = (
        DisksProgressBar(total, "Umounting", _format_umount_result)
        if use_progress
        else None
    )

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(display_names.get(slug, slug))

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(
                display_names.get(slug, slug),
                result.success,
                result.detail,
                result.warning,
            )

    results = umount_volumes(
        cfg,
        resolved,
        names=name,
        on_umount_start=on_umount_start,
        on_umount_end=on_umount_end,
    )
    if umount_bar is not None:
        umount_bar.stop()

    _probe_and_show_status(cfg, resolved, output, name)

    if any(not r.success for r in results):
        raise typer.Exit(1)
