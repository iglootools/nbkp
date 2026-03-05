"""Typer CLI: run and status commands."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal, Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import (
    Config,
    ConfigError,
    EndpointFilter,
    ResolvedEndpoints,
    load_config,
    resolve_all_endpoints,
)
from .check import (
    SyncReason,
    SyncStatus,
    VolumeStatus,
    check_all_syncs,
)
from .sync.btrfs import (
    list_snapshots,
    prune_snapshots as btrfs_prune_snapshots,
)
from .sync.hardlinks import (
    prune_snapshots as hl_prune_snapshots,
)
from .output import (
    OutputFormat,
    print_config_error,
    print_human_config,
    print_human_prune_results,
    print_human_results,
    print_human_check,
    print_human_troubleshoot,
)
from .democli import app as demo_app
from .scriptgen import ScriptOptions, generate_script
from .sync import (
    ProgressMode,
    PruneResult,
    SyncResult,
    run_all_syncs,
)

_SENTINEL_ONLY_REASONS = {
    SyncReason.SOURCE_SENTINEL_NOT_FOUND,
    SyncReason.DESTINATION_SENTINEL_NOT_FOUND,
}

app = typer.Typer(
    name="nbkp",
    help="Nomad Backup",
    no_args_is_help=True,
)

config_app = typer.Typer(
    name="config",
    help="Configuration commands",
    no_args_is_help=True,
)
app.add_typer(config_app)

app.add_typer(
    demo_app,
    name="demo",
    help="Demo CLI: sample output rendering and seed data",
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
            help=(
                "Exit non-zero on any inactive sync,"
                " including missing sentinels"
            ),
        ),
    ] = False,
    locations: Annotated[
        Optional[list[str]],
        typer.Option(
            "--locations",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Prefer private (LAN) endpoints",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Prefer public (WAN) endpoints",
        ),
    ] = False,
) -> None:
    """Check status of volumes and syncs."""
    cfg = _load_config_or_exit(config)
    resolved = _resolve_endpoints(cfg, locations, private, public)
    output_format = output
    vol_statuses, sync_statuses, has_errors = _check_and_display(
        cfg,
        output_format,
        strict,
        resolved_endpoints=resolved,
    )

    if output_format is OutputFormat.JSON:
        data = {
            "volumes": [v.model_dump() for v in vol_statuses.values()],
            "syncs": [s.model_dump() for s in sync_statuses.values()],
        }
        typer.echo(json.dumps(data, indent=2))

    if has_errors:
        raise typer.Exit(1)


@config_app.command()
def show(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
) -> None:
    """Show parsed configuration."""
    cfg = _load_config_or_exit(config)
    output_format = output
    match output_format:
        case OutputFormat.JSON:
            typer.echo(json.dumps(cfg.model_dump(by_alias=True), indent=2))
        case OutputFormat.HUMAN:
            resolved = resolve_all_endpoints(cfg)
            print_human_config(cfg, resolved_endpoints=resolved)


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
            help=("Progress mode: none, overall," " per-file, or full"),
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
            help=(
                "Exit non-zero on any inactive sync,"
                " including missing sentinels"
            ),
        ),
    ] = False,
    locations: Annotated[
        Optional[list[str]],
        typer.Option(
            "--locations",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Prefer private (LAN) endpoints",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Prefer public (WAN) endpoints",
        ),
    ] = False,
) -> None:
    """Run backup syncs."""
    cfg = _load_config_or_exit(config)
    resolved = _resolve_endpoints(cfg, locations, private, public)
    output_format = output
    vol_statuses, sync_statuses, has_errors = _check_and_display(
        cfg,
        output_format,
        strict,
        only_syncs=sync,
        resolved_endpoints=resolved,
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
            on_sync_end=(
                on_sync_end if output_format is OutputFormat.HUMAN else None
            ),
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
                print_human_results(results, dry_run)

        if any(not r.success for r in results):
            raise typer.Exit(1)


@app.command()
def sh(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    output_file: Annotated[
        Optional[str],
        typer.Option(
            "--output-file",
            "-o",
            help="Write script to file (made executable)",
        ),
    ] = None,
    relative_src: Annotated[
        bool,
        typer.Option(
            "--relative-src",
            help=(
                "Make source paths relative to"
                " script location"
                " (requires --output-file)"
            ),
        ),
    ] = False,
    relative_dst: Annotated[
        bool,
        typer.Option(
            "--relative-dst",
            help=(
                "Make destination paths relative to"
                " script location"
                " (requires --output-file)"
            ),
        ),
    ] = False,
    locations: Annotated[
        Optional[list[str]],
        typer.Option(
            "--locations",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Prefer private (LAN) endpoints",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Prefer public (WAN) endpoints",
        ),
    ] = False,
) -> None:
    """Generate a standalone backup shell script.

    This is useful for deploying to systems without Python,
    or auditing what commands will run.
    """
    if (relative_src or relative_dst) and output_file is None:
        typer.echo(
            "Error: --relative-src/--relative-dst" " require --output-file",
            err=True,
        )
        raise typer.Exit(2)

    cfg = _load_config_or_exit(config)
    resolved = _resolve_endpoints(cfg, locations, private, public)
    script = generate_script(
        cfg,
        ScriptOptions(
            config_path=config,
            output_file=(
                os.path.abspath(output_file) if output_file else None
            ),
            relative_src=relative_src,
            relative_dst=relative_dst,
        ),
        resolved_endpoints=resolved,
    )
    if output_file is not None:
        path = Path(output_file)
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        typer.echo(f"Written to {output_file}", err=True)
    else:
        typer.echo(script)


@app.command()
def troubleshoot(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    locations: Annotated[
        Optional[list[str]],
        typer.Option(
            "--locations",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Prefer private (LAN) endpoints",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Prefer public (WAN) endpoints",
        ),
    ] = False,
) -> None:
    """Diagnose issues and show how to fix them."""
    cfg = _load_config_or_exit(config)
    resolved = _resolve_endpoints(cfg, locations, private, public)
    vol_statuses, sync_statuses = _check_all_with_progress(
        cfg,
        use_progress=True,
        resolved_endpoints=resolved,
    )
    print_human_troubleshoot(
        vol_statuses,
        sync_statuses,
        cfg,
        resolved_endpoints=resolved,
    )


@app.command()
def prune(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    sync: Annotated[
        Optional[list[str]],
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
    locations: Annotated[
        Optional[list[str]],
        typer.Option(
            "--locations",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Prefer private (LAN) endpoints",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Prefer public (WAN) endpoints",
        ),
    ] = False,
) -> None:
    """Prune old snapshots beyond max-snapshots limit."""
    cfg = _load_config_or_exit(config)
    resolved = _resolve_endpoints(cfg, locations, private, public)
    output_format = output
    _, sync_statuses = _check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        resolved_endpoints=resolved,
    )

    def _is_prunable(slug: str, status: SyncStatus) -> bool:
        if sync and slug not in sync:
            return False
        if not status.active:
            return False
        dst = status.config.destination
        match dst.snapshot_mode:
            case "btrfs":
                return dst.btrfs_snapshots.max_snapshots is not None
            case "hard-link":
                return dst.hard_link_snapshots.max_snapshots is not None
            case _:
                return False

    prunable = [
        (slug, status)
        for slug, status in sync_statuses.items()
        if _is_prunable(slug, status)
    ]

    results: list[PruneResult] = []
    for slug, status in prunable:
        dst = status.config.destination
        try:
            match dst.snapshot_mode:
                case "btrfs":
                    assert dst.btrfs_snapshots.max_snapshots is not None
                    deleted = btrfs_prune_snapshots(
                        status.config,
                        cfg,
                        dst.btrfs_snapshots.max_snapshots,
                        dry_run=dry_run,
                        resolved_endpoints=resolved,
                    )
                case "hard-link":
                    assert dst.hard_link_snapshots.max_snapshots is not None
                    deleted = hl_prune_snapshots(
                        status.config,
                        cfg,
                        dst.hard_link_snapshots.max_snapshots,
                        dry_run=dry_run,
                        resolved_endpoints=resolved,
                    )
                case _:
                    deleted = []
            remaining = list_snapshots(status.config, cfg, resolved)
            results.append(
                PruneResult(
                    sync_slug=slug,
                    deleted=deleted,
                    kept=(len(remaining) + (len(deleted) if dry_run else 0)),
                    dry_run=dry_run,
                )
            )
        except RuntimeError as e:
            results.append(
                PruneResult(
                    sync_slug=slug,
                    deleted=[],
                    kept=0,
                    dry_run=dry_run,
                    detail=str(e),
                )
            )

    match output_format:
        case OutputFormat.JSON:
            typer.echo(
                json.dumps(
                    [r.model_dump() for r in results],
                    indent=2,
                )
            )
        case OutputFormat.HUMAN:
            print_human_prune_results(results, dry_run)

    if any(r.detail for r in results):
        raise typer.Exit(1)


def _load_config_or_exit(
    config_path: str | None,
) -> Config:
    """Load config or exit with code 2 on error."""
    try:
        return load_config(config_path)
    except ConfigError as e:
        print_config_error(e)
        raise typer.Exit(2)


def _build_endpoint_filter(
    locations: list[str] | None,
    private: bool,
    public: bool,
) -> EndpointFilter | None:
    """Build an EndpointFilter from CLI options."""
    network: Literal["private", "public"] | None = None
    if private:
        network = "private"
    elif public:
        network = "public"
    locs = locations or []
    if not locs and network is None:
        return None
    return EndpointFilter(locations=locs, network=network)


def _resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    private: bool,
    public: bool,
) -> ResolvedEndpoints:
    """Build filter and resolve all endpoints once."""
    ef = _build_endpoint_filter(locations, private, public)
    return resolve_all_endpoints(cfg, ef)


def _check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Run check_all_syncs with an optional progress bar."""
    total = len(cfg.volumes) + len(cfg.syncs)
    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("{task.completed}/{task.total}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Checking volumes and syncs...", total=total)

        def on_progress(_slug: str) -> None:
            progress.advance(task)

        return check_all_syncs(
            cfg,
            on_progress=on_progress,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
        )


def _check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strict: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> tuple[
    dict[str, VolumeStatus],
    dict[str, SyncStatus],
    bool,
]:
    """Compute statuses, display human output, and check for errors.

    Returns volume statuses, sync statuses, and whether there are
    fatal errors.  When *only_syncs* is given, only those syncs
    (and the volumes they reference) are checked.
    """
    vol_statuses, sync_statuses = _check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            vol_statuses,
            sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
        )

    if strict:
        has_errors = any(not s.active for s in sync_statuses.values())
    else:
        has_errors = any(
            set(s.reasons) - _SENTINEL_ONLY_REASONS
            for s in sync_statuses.values()
        )

    return vol_statuses, sync_statuses, has_errors


def main() -> None:
    """Main CLI entry point."""
    app()


if __name__ == "__main__":
    main()
