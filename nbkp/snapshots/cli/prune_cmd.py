from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ...clidisplay import OutputFormat, echo_json
from ...clidisplay.invocation import Invocation, format_troubleshoot_hint
from ...cliwiring.config import (
    load_config_or_exit,
    require_known_syncs,
    resolve_endpoints,
)
from ...cliwiring.mount import managed_mount
from ...cliwiring.preflight import check_all_with_progress
from ...cliwiring.runtime import runtime_of
from ...policy import Strictness
from ...remote.endpoints import NetworkType
from .. import prune_all_syncs
from ..disposition import failed_in_preflight, has_errors
from ..models import PruneResult
from ..output import format_human_prune_results
from . import app


@app.command("prune")
def prune_cmd(
    ctx: typer.Context,
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
    sync: Annotated[
        list[str] | None,
        typer.Option("--sync", "-s", help="Sync name(s) to prune"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Perform a dry run"),
    ] = False,
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
                " ignore-none (inactive syncs fail),"
                " ignore-inactive (skip expected-inactive, default),"
                " ignore-all (skip syncs with preflight errors)"
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
            help="Mount/umount volumes with mount config",
        ),
    ] = True,
    umount: Annotated[
        bool,
        typer.Option(
            "--umount/--no-umount",
            help="Umount after prune (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Remove snapshots beyond the `max-snapshots` limit. Normally handled automatically by `run`, but can be invoked manually."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_syncs(cfg, sync, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    with managed_mount(
        cfg,
        resolved,
        runtime=runtime,
        mount=mount,
        umount=umount,
        output_format=output,
        strictness=strictness,
    ) as (cfg, mount_observations):
        preflight = check_all_with_progress(
            cfg,
            use_progress=output is OutputFormat.HUMAN,
            only_syncs=sync,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            strictness=strictness,
            exclude_locations=exclude_location or [],
            runtime=runtime,
        )
        results = prune_all_syncs(
            cfg,
            preflight.sync_statuses,
            dry_run=dry_run,
            only_syncs=sync,
            resolved_endpoints=resolved,
            strictness=strictness,
            run=runtime.run,
        )
        _print_results(results, dry_run, output)
        if has_errors(results):
            if output is OutputFormat.HUMAN and failed_in_preflight(
                results, preflight.sync_statuses
            ):
                Console(stderr=True).print(
                    format_troubleshoot_hint(
                        Invocation.of(
                            config,
                            location,
                            exclude_location,
                            network.value if network else None,
                        )
                    ),
                    soft_wrap=True,
                )
            raise typer.Exit(1)


def _print_results(
    results: list[PruneResult], dry_run: bool, output: OutputFormat
) -> None:
    match output:
        case OutputFormat.JSON:
            echo_json(list(results))
        case OutputFormat.HUMAN:
            Console().print(format_human_prune_results(results, dry_run))
