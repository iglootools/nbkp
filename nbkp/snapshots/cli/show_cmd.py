"""CLI show command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat
from ...config.cli.helpers import load_config_or_exit, resolve_endpoints
from ...config.epresolution import NetworkType
from ..cmd_handler import show_all_syncs
from ..output import print_human_show_results

from ...disks.cli.helpers import managed_mount
from ...preflight.cli.helpers import check_all_with_progress

from . import app


@app.command()
def show(
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
    sync: Annotated[
        Optional[list[str]],
        typer.Option("--sync", "-s", help="Sync name(s) to show"),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
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
            help="Mount/umount volumes with mount config",
        ),
    ] = True,
    umount: Annotated[
        bool,
        typer.Option(
            "--umount/--no-umount",
            help="Umount after show (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Display snapshot information for each sync endpoint."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    output_format = output

    with managed_mount(
        cfg, resolved, mount=mount, umount=umount, output_format=output_format
    ) as (_mount_strategy, mount_observations):
        preflight = check_all_with_progress(
            cfg,
            use_progress=output_format is OutputFormat.HUMAN,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )

        results = show_all_syncs(
            cfg,
            preflight.sync_statuses,
            only_syncs=sync,
            resolved_endpoints=resolved,
        )

        match output_format:
            case OutputFormat.JSON:
                typer.echo(
                    json.dumps(
                        [r.model_dump(mode="json") for r in results],
                        indent=2,
                    )
                )
            case OutputFormat.HUMAN:
                print_human_show_results(results)

        if any(r.detail and not r.skipped for r in results):
            raise typer.Exit(1)
