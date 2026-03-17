"""CLI troubleshoot command."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from ..config.epresolution import NetworkType
from ..preflight.output import print_human_troubleshoot
from .app import app
from .common import (
    check_all_with_progress,
    load_config_or_exit,
    managed_mount,
    resolve_endpoints,
)


@app.command()
def troubleshoot(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--location",
            "-l",
            help="Prefer endpoints at these locations",
        ),
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
    mount: Annotated[
        bool,
        typer.Option(
            "--mount/--no-mount",
            help="Mount/umount volumes with mount config before checking",
        ),
    ] = True,
    umount: Annotated[
        bool,
        typer.Option(
            "--umount/--no-umount",
            help="Umount after check (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Run the same checks as `check` but displays step-by-step fix instructions for every failure. Useful when `check` reports problems."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    with managed_mount(cfg, resolved, mount=mount, umount=umount) as (
        _mount_strategy,
        mount_observations,
    ):
        ssh_statuses, vol_statuses, sync_statuses = check_all_with_progress(
            cfg,
            use_progress=True,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )
        print_human_troubleshoot(
            ssh_statuses,
            vol_statuses,
            sync_statuses,
            cfg,
            resolved_endpoints=resolved,
        )
