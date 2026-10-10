"""Snapshot output formatting (prune and show results)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .models import PruneResult, ShowResult, SnapshotSkipReason


def retention_display(max_snapshots: int | None) -> str:
    """Format a max_snapshots value for display."""
    if max_snapshots is None:
        return "unlimited"
    else:
        return f"keep {max_snapshots}"


# ---------------------------------------------------------------------------
# Prune results
# ---------------------------------------------------------------------------


def _status_text(skip_reason: SnapshotSkipReason | None, error: str | None) -> Text:
    """Map a result's skip reason / error to a styled Rich Text label.

    Text, not markup: the error carries remote stderr, which may contain
    square brackets.
    """
    match (skip_reason, error):
        case (_, str() as message):
            return Text(f"FAILED ({message})", style="red")
        case (SnapshotSkipReason() as reason, None):
            return Text(f"SKIPPED ({reason.value})", style="dim")
        case _:
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
        status = _status_text(r.skip_reason, r.error)
        table.add_row(
            r.sync_slug,
            str(len(r.deleted)),
            str(r.kept),
            status,
        )

    c.print(table)


# ---------------------------------------------------------------------------
# Show results
# ---------------------------------------------------------------------------


def _show_row(r: ShowResult) -> tuple[str, str, str, str, str, Text]:
    """Table cells for one show result; ``--`` when snapshots are off."""
    status = _status_text(r.skip_reason, r.error)
    match r.snapshot_mode:
        case "none":
            return (r.sync_slug, "--", "--", "--", "--", status)
        case mode:
            return (
                r.sync_slug,
                mode,
                str(len(r.snapshots)),
                r.latest.name if r.latest else "--",
                retention_display(r.max_snapshots),
                status,
            )


def print_human_show_results(
    results: list[ShowResult],
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable snapshot show results."""
    c = console or Console()

    table = Table(title="Snapshots:")
    table.add_column("Name", style="bold")
    table.add_column("Mode")
    table.add_column("Snapshots")
    table.add_column("Latest")
    table.add_column("Retention")
    table.add_column("Status")

    for r in results:
        table.add_row(*_show_row(r))

    c.print(table)
