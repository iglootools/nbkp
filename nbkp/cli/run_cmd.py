"""CLI run command."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text

from ..config.epresolution import NetworkType
from ..ordering.output import build_rich_tree_sections
from ..preflight.output import print_human_check
from ..preflight import SyncStatus, VolumeStatus
from ..sync import ProgressMode, SyncResult
from ..sync.output import build_human_results_sections
from ..sync.pipeline import check_and_run
from .app import app
from .common import (
    OutputFormat,
    _check_total,
    load_config_or_exit,
    managed_mount,
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
        check_progress_ctx: Progress | None = None
        check_task_id = None

        if output_format is OutputFormat.HUMAN:
            total = _check_total(cfg, sync)
            if total > 0:
                check_progress_ctx = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    TextColumn("{task.completed}/{task.total}"),
                    transient=True,
                )
                check_progress_ctx.start()
                check_task_id = check_progress_ctx.add_task(
                    "Checking volumes, endpoints, and syncs...", total=total
                )

        def on_check_progress(_slug: str) -> None:
            if check_progress_ctx is not None and check_task_id is not None:
                check_progress_ctx.advance(check_task_id)

        def on_checks_done(
            vol_statuses: dict[str, VolumeStatus],
            sync_statuses: dict[str, SyncStatus],
        ) -> None:
            if check_progress_ctx is not None:
                check_progress_ctx.stop()
            if output_format is OutputFormat.HUMAN:
                print_human_check(
                    vol_statuses, sync_statuses, cfg, resolved_endpoints=resolved
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
            strict=strict,
            dry_run=dry_run,
            only_syncs=sync,
            progress=progress,
            prune=prune,
            on_check_progress=on_check_progress,
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
                    if strict
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
                    f" sync{'s' if len(errored) != 1 else ''}:\n"
                    + "\n".join(lines)
                    + "\n\nRun [bold]nbkp troubleshoot[/bold] for"
                    " detailed remediation steps."
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
