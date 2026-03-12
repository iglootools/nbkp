"""Sync run/prune output formatting."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
)
from .runner import PruneResult, SyncOutcome, SyncResult


def _format_snapshot_display(
    snapshot_path: str,
    sync_slug: str,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Format a snapshot path with SSH URI prefix for remote volumes."""
    sync = config.syncs[sync_slug]
    dst_ep = config.destination_endpoint(sync)
    vol = config.volumes[dst_ep.volume]
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return f"{ep.server.host}:{snapshot_path}"
        case LocalVolume():
            return snapshot_path


def print_human_results(
    results: list[SyncResult],
    dry_run: bool,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable run results."""
    if console is None:
        console = Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"Sync results{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for r in results:
        match r.outcome:
            case SyncOutcome.SUCCESS:
                status = Text("OK", style="green")
            case SyncOutcome.CANCELLED:
                status = Text("CANCELLED", style="yellow")
            case SyncOutcome.SKIPPED:
                status = Text("SKIPPED", style="dim")
            case SyncOutcome.FAILED:
                status = Text("FAILED", style="red")

        details_parts: list[str] = []
        if r.detail:
            details_parts.append(f"Error: {r.detail}")
        if r.snapshot_path:
            display = _format_snapshot_display(
                r.snapshot_path,
                r.sync_slug,
                config,
                resolved_endpoints,
            )
            details_parts.append(f"Snapshot: {display}")
        if r.pruned_paths:
            details_parts.append(f"Pruned: {len(r.pruned_paths)} snapshot(s)")
        if r.output and not r.success:
            lines = r.output.strip().split("\n")[:5]
            details_parts.extend(lines)

        table.add_row(
            r.sync_slug,
            status,
            "\n".join(details_parts),
        )

    console.print(table)


def print_human_prune_results(
    results: list[PruneResult],
    dry_run: bool,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable prune results."""
    if console is None:
        console = Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"NBKP prune{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Deleted")
    table.add_column("Kept")
    table.add_column("Status")

    for r in results:
        if r.skipped:
            status = Text(f"SKIPPED ({r.detail})", style="dim")
        elif r.detail:
            status = Text("FAILED", style="red")
        else:
            status = Text("OK", style="green")

        table.add_row(
            r.sync_slug,
            str(len(r.deleted)),
            str(r.kept),
            status,
        )

    console.print(table)
