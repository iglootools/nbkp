"""Disks mount command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat, DisksProgressBar, load_config_or_exit
from ...clihelpers.endpoints import resolve_endpoints
from ...config.epresolution import NetworkType
from ...credentials import build_passphrase_fn
from ..lifecycle import MountResult, mount_volume_count, mount_volumes
from ..observation import build_mount_observations
from ..output import volume_display_name
from . import app
from .helpers import (
    _error_status,
    _format_mount_result,
    _show_status_table,
    _unmanaged_statuses,
)


@app.command("mount")
def volumes_mount(
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
        typer.Option("--name", "-n", help="Volume name(s) to mount"),
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
    """Attach LUKS and mount volumes. Mounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )

    use_progress = output == OutputFormat.HUMAN
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }
    total = mount_volume_count(cfg, name)
    mount_bar = (
        DisksProgressBar(total, "Mounting", _format_mount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(display_names.get(slug, slug))

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(
                display_names.get(slug, slug), result.success, result.detail
            )

    try:
        strategies, results = mount_volumes(
            cfg,
            resolved,
            passphrase_fn,
            names=name,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
        )
    finally:
        if mount_bar is not None:
            mount_bar.stop()
        cache.clear()

    observations = build_mount_observations(results, strategies, cfg)
    statuses = [
        *((display_names.get(slug, slug), obs) for slug, obs in observations.items()),
        *(
            (display_names.get(r.volume_slug, r.volume_slug), _error_status(r.detail))
            for r in results
            if not r.success and r.volume_slug not in observations
        ),
        *_unmanaged_statuses(cfg, name),
    ]
    _show_status_table(statuses, output)

    if any(not r.success for r in results):
        raise typer.Exit(1)
