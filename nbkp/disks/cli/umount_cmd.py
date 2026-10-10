"""Disks umount command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ...clihelpers import OutputFormat
from ...config import Config
from ...config.cli.helpers import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType, ResolvedEndpoints
from ..lifecycle import UmountResult, umount_volumes
from ..plan import plan_lifecycle
from . import app
from .helpers import _probe_and_show_status, require_known_names
from .helpers.lifecycle_progress import LifecycleProgress, mount_display_names
from .helpers.plan_output import show_plan


@app.command("umount")
def umount(
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
        typer.Option("--name", "-n", help="Volume name(s) to umount"),
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
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Report what would be unmounted and locked without changing"
                " anything (no udisksctl unmount/lock)."
                " Long form only: -n is --name here."
            ),
        ),
    ] = False,
) -> None:
    """Umount volumes and lock LUKS. Umounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    if dry_run:
        show_plan(
            plan_lifecycle(cfg, resolved, mounting=False, names=name),
            mount_display_names(cfg),
            output,
        )
        return

    results = _umount_with_progress(cfg, resolved, name, output)
    _probe_and_show_status(cfg, resolved, output, name)
    if any(not r.success for r in results):
        raise typer.Exit(1)


def _umount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
) -> list[UmountResult]:
    """Umount and lock, with a progress bar for humans."""
    progress = LifecycleProgress.create(
        cfg,
        enabled=output is OutputFormat.HUMAN,
        names=names,
        credentials=False,
        mounting=False,
    )
    try:
        return umount_volumes(
            cfg,
            resolved,
            names=names,
            on_umount_start=progress.on_umount_start,
            on_umount_end=progress.on_umount_end,
        )
    finally:
        progress.stop_all()
