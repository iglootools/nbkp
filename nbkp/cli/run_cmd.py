"""CLI run command."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer
from rich.console import Console

from ..config import NetworkType
from ..ordering.output import print_rich_tree_graph
from ..output import OutputFormat
from ..sync import (
    ProgressMode,
    SyncResult,
    run_all_syncs,
)
from ..sync.output import print_human_results
from .app import app
from .common import (
    _INACTIVE_ERRORS,
    check_and_display,
    load_config_or_exit,
    resolve_endpoints,
)


@app.command()
def run(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Perform a dry run"),
    ] = False,
    sync: Annotated[
        Optional[list[str]],
        typer.Option("--sync", "-s", help="Sync name(s) to run"),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    progress: Annotated[
        Optional[ProgressMode],
        typer.Option(
            "--progress",
            "-p",
            help=("Progress mode: none, overall, per-file, or full"),
        ),
    ] = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune/--no-prune",
            help="Prune old snapshots after sync",
        ),
    ] = True,
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
) -> None:
    """Execute all active syncs in dependency order. Supports dry-run, progress display, snapshot creation, and automatic pruning."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    output_format = output
    vol_statuses, sync_statuses, has_errors = check_and_display(
        cfg,
        output_format,
        strict,
        only_syncs=sync,
        resolved_endpoints=resolved,
        dry_run=dry_run,
    )

    if has_errors:
        if output_format is OutputFormat.JSON:
            data = {
                "volumes": [v.model_dump() for v in vol_statuses.values()],
                "syncs": [s.model_dump() for s in sync_statuses.values()],
                "results": [],
            }
            typer.echo(json.dumps(data, indent=2))
        raise typer.Exit(1)
    else:
        if output_format is OutputFormat.HUMAN:
            typer.echo("")
            print_rich_tree_graph(cfg)
            typer.echo("")

        use_spinner = output_format is OutputFormat.HUMAN and progress in (
            None,
            ProgressMode.NONE,
        )
        stream_output = (
            (lambda chunk: typer.echo(chunk, nl=False))
            if output_format is OutputFormat.HUMAN and not use_spinner
            else None
        )

        console = Console()
        status_display = None

        def on_sync_start(slug: str) -> None:
            nonlocal status_display
            if use_spinner:
                status_display = console.status(f"Syncing {slug}...")
                status_display.start()
            else:
                console.print(f"Syncing {slug}...")

        def on_sync_end(slug: str, result: SyncResult) -> None:
            nonlocal status_display
            if status_display is not None:
                status_display.stop()
                status_display = None
            icon = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            console.print(f"{icon} {slug}")

        results = run_all_syncs(
            cfg,
            sync_statuses,
            dry_run=dry_run,
            only_syncs=sync,
            progress=progress,
            prune=prune,
            on_rsync_output=stream_output,
            on_sync_start=(
                on_sync_start if output_format is OutputFormat.HUMAN else None
            ),
            on_sync_end=(on_sync_end if output_format is OutputFormat.HUMAN else None),
            resolved_endpoints=resolved,
        )

        match output_format:
            case OutputFormat.JSON:
                data = {
                    "volumes": [v.model_dump() for v in vol_statuses.values()],
                    "syncs": [s.model_dump() for s in sync_statuses.values()],
                    "results": [r.model_dump() for r in results],
                }
                typer.echo(json.dumps(data, indent=2))
            case OutputFormat.HUMAN:
                typer.echo("")
                print_human_results(results, dry_run, cfg, resolved)

        def _is_expected_skip(r: SyncResult) -> bool:
            ss = sync_statuses.get(r.sync_slug)
            return (
                ss is not None
                and bool(ss.errors)
                and set(ss.errors) <= _INACTIVE_ERRORS
            )

        if any(not r.success and not _is_expected_skip(r) for r in results):
            raise typer.Exit(1)
