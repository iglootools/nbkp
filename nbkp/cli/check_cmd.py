"""CLI check command."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from ..config import NetworkType
from ..output import OutputFormat
from .app import app
from .common import (
    check_and_display,
    load_config_or_exit,
    managed_mount,
    resolve_endpoints,
)


@app.command()
def check(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help=("Exit non-zero on any inactive sync, including missing sentinels"),
        ),
    ] = False,
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
    """Verify that volumes are reachable, sentinel files exist, SSH connectivity works, and required tools are available. Use this before `run` to confirm everything is ready."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    output_format = output

    with managed_mount(
        cfg, resolved, mount=mount, umount=umount, output_format=output_format
    ) as (_mount_strategy, mount_observations):
        vol_statuses, sync_statuses, has_errors = check_and_display(
            cfg,
            output_format,
            strict,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )

        if output_format is OutputFormat.JSON:
            data = {
                "volumes": [v.model_dump() for v in vol_statuses.values()],
                "syncs": [s.model_dump() for s in sync_statuses.values()],
            }
            typer.echo(json.dumps(data, indent=2))

        if has_errors:
            raise typer.Exit(1)
