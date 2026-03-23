"""CLI run command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from ..config.epresolution import NetworkType
from ..ordering.output import build_rich_tree_sections
from ..preflight import PreflightResult
from ..preflight.output import print_human_check
from ..sync import ProgressMode, SyncResult
from ..sync.output import build_human_results_sections
from ..sync.pipeline import Strictness, check_and_run
from .app import app
from .common import (
    CheckProgressBar,
    OutputFormat,
    _check_total,
    load_config_or_exit,
    managed_mount,
    resolve_endpoints,
)


@app.command()
def run(
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
            help="Umount after sync (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Execute all active syncs in dependency order. Supports dry-run, progress display, snapshot creation, and automatic pruning."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    output_format = output

    with managed_mount(
        cfg, resolved, mount=mount, umount=umount, output_format=output_format
    ) as (_mount_strategy, mount_observations):
        # ── Check progress bar ────────────────────────────────────
        total = _check_total(cfg, sync) if output_format is OutputFormat.HUMAN else 0
        check_bar = (
            CheckProgressBar(total)
            if output_format is OutputFormat.HUMAN and total > 0
            else None
        )

        def on_check_start(label: str) -> None:
            if check_bar is not None:
                check_bar.on_start(label)

        def on_check_end(label: str, active: bool, error_summary: str | None) -> None:
            if check_bar is not None:
                check_bar.on_end(label, active, error_summary)

        def on_checks_done(preflight: PreflightResult) -> None:
            if check_bar is not None:
                check_bar.stop()
            if output_format is OutputFormat.HUMAN:
                print_human_check(
                    preflight.ssh_endpoint_statuses,
                    preflight.volume_statuses,
                    preflight.sync_statuses,
                    cfg,
                    resolved_endpoints=resolved,
                )

        # ── Sync progress callbacks ───────────────────────────────
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
        progress_lines: list[Text] = []
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
            icon = "[green]\u2713[/green]" if result.success else "[red]\u2717[/red]"
            console.print(f"{icon} {slug}")
            progress_lines.append(Text.from_markup(f"{icon} {slug}"))

        # ── Pipeline ──────────────────────────────────────────────
        pipeline = check_and_run(
            cfg,
            strictness=strictness,
            dry_run=dry_run,
            only_syncs=sync,
            progress=progress,
            prune=prune,
            on_check_start=on_check_start,
            on_check_end=on_check_end,
            on_checks_done=on_checks_done,
            on_rsync_output=stream_output,
            on_sync_start=(
                on_sync_start if output_format is OutputFormat.HUMAN else None
            ),
            on_sync_end=(on_sync_end if output_format is OutputFormat.HUMAN else None),
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )

        # ── Output ────────────────────────────────────────────────
        if pipeline.has_preflight_errors:
            if output_format is OutputFormat.JSON:
                data = {
                    "volumes": [v.model_dump() for v in pipeline.vol_statuses.values()],
                    "syncs": [s.model_dump() for s in pipeline.sync_statuses.values()],
                    "results": [],
                }
                typer.echo(json.dumps(data, indent=2))
            else:
                errored = (
                    {
                        slug: sorted(e.value for e in s.errors)
                        for slug, s in pipeline.sync_statuses.items()
                        if not s.active
                    }
                    if strictness is Strictness.IGNORE_NONE
                    else {
                        slug: sorted(e.value for e in s.errors)
                        for slug, s in pipeline.sync_statuses.items()
                        if not s.active and not s.is_expected_inactive()
                    }
                )
                lines = [
                    f"  {slug}: {', '.join(errors)}" for slug, errors in errored.items()
                ]
                Console(stderr=True).print(
                    f"\n[bold red]Aborting:[/bold red] preflight checks"
                    f" found errors in {len(errored)}"
                    f" sync{'s' if len(errored) != 1 else ''}:\n" + "\n".join(lines)
                )
        else:
            match output_format:
                case OutputFormat.JSON:
                    data = {
                        "volumes": [
                            v.model_dump() for v in pipeline.vol_statuses.values()
                        ],
                        "syncs": [
                            s.model_dump() for s in pipeline.sync_statuses.values()
                        ],
                        "results": [r.model_dump() for r in pipeline.results],
                    }
                    typer.echo(json.dumps(data, indent=2))
                case OutputFormat.HUMAN:
                    sections = [
                        *build_rich_tree_sections(cfg),
                        Text(""),
                        *progress_lines,
                        Text(""),
                        *build_human_results_sections(
                            pipeline.results, dry_run, cfg, resolved
                        ),
                    ]
                    console.print(
                        Panel(
                            Group(*sections),
                            title="[bold]Sync Results[/bold]",
                            border_style="cyan",
                            padding=(0, 1),
                        )
                    )

        if pipeline.has_preflight_errors or pipeline.has_sync_failures:
            raise typer.Exit(1)
