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


def _outcome_text(outcome: SyncOutcome) -> Text:
    """Map a sync outcome to a styled Rich Text label."""
    match outcome:
        case SyncOutcome.SUCCESS:
            return Text("OK", style="green")
        case SyncOutcome.CANCELLED:
            return Text("CANCELLED", style="yellow")
        case SyncOutcome.SKIPPED:
            return Text("SKIPPED", style="dim")
        case SyncOutcome.FAILED:
            return Text("FAILED", style="red")


def print_human_results(
    results: list[SyncResult],
    dry_run: bool,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable run results."""
    c = console or Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"Sync results{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for r in results:
        status = _outcome_text(r.outcome)
        details_parts = [
            *([f"Error: {r.detail}"] if r.detail else []),
            *(
                [
                    f"Snapshot: {_format_snapshot_display(r.snapshot_path, r.sync_slug, config, resolved_endpoints)}"
                ]
                if r.snapshot_path
                else []
            ),
            *([f"Pruned: {len(r.pruned_paths)} snapshot(s)"] if r.pruned_paths else []),
            *(r.output.strip().split("\n")[:5] if r.output and not r.success else []),
        ]

        table.add_row(
            r.sync_slug,
            status,
            "\n".join(details_parts),
        )

    c.print(table)


def print_human_prune_results(
    results: list[PruneResult],
    dry_run: bool,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable prune results."""
    c = console or Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"NBKP prune{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Deleted")
    table.add_column("Kept")
    table.add_column("Status")

    for r in results:
        status = _prune_status_text(r)
        table.add_row(
            r.sync_slug,
            str(len(r.deleted)),
            str(r.kept),
            status,
        )

    c.print(table)


def _prune_status_text(r: PruneResult) -> Text:
    """Map a prune result to a styled Rich Text label."""
    if r.skipped:
        return Text(f"SKIPPED ({r.detail})", style="dim")
    elif r.detail:
        return Text("FAILED", style="red")
    else:
        return Text("OK", style="green")
