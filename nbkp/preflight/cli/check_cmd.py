"""CLI check command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ...clihelpers import OutputFormat, echo_json
from ...config.cli.helpers import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType
from ...disks.cli.helpers import managed_mount
from ..strictness import Strictness
from . import app
from .helpers import check_and_display


@app.command()
def check(
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
    strictness: Annotated[
        Strictness,
        typer.Option(
            "--strictness",
            "-S",
            help=(
                "How to handle preflight errors:"
                " ignore-none (all errors fatal),"
                " ignore-inactive (skip expected-inactive, default),"
                " ignore-all (ignore all errors)"
            ),
        ),
    ] = Strictness.IGNORE_INACTIVE,
    location: Annotated[
        list[str] | None,
        typer.Option(
            "--location",
            "-l",
            help="Prefer endpoints at these locations",
        ),
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
    ) as (cfg, mount_observations):
        preflight, has_errors = check_and_display(
            cfg,
            output_format,
            strictness,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )

        if output_format is OutputFormat.JSON:
            echo_json(
                {
                    "volumes": list(preflight.volume_statuses.values()),
                    "syncs": list(preflight.sync_statuses.values()),
                }
            )

        if has_errors:
            raise typer.Exit(1)
