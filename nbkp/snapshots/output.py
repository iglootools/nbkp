"""Snapshot output formatting (prune results)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .models import PruneResult


def retention_display(max_snapshots: int | None) -> str:
    """Format a max_snapshots value for display."""
    if max_snapshots is None:
        return "unlimited"
    else:
        return f"keep {max_snapshots}"


# ---------------------------------------------------------------------------
# Prune results
# ---------------------------------------------------------------------------


def _prune_status_text(r: PruneResult) -> Text:
    """Map a prune result to a styled Rich Text label."""
    if r.skipped:
        return Text(f"SKIPPED ({r.detail})", style="dim")
    elif r.detail:
        return Text("FAILED", style="red")
    else:
        return Text("OK", style="green")


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
