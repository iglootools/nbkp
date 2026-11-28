"""Render snapshot show and prune results as human-readable text and tables."""

from rich.table import Table
from rich.text import Text

from nbkp.preflight.output import format_problems
from nbkp.snapshots.errors import SnapshotOp
from nbkp.snapshots.models import (
    PruneResult,
    ShowResult,
    SnapshotErrorKind,
    SnapshotResultError,
    SnapshotSkipReason,
)
from nbkp.theme import dry_run_table_title


def retention_display(max_snapshots: int | None) -> str:
    """Return the retention column text: ``unlimited`` or ``keep N``."""
    if max_snapshots is None:
        return "unlimited"
    else:
        return f"keep {max_snapshots}"


_OPERATIONS: dict[SnapshotOp, str] = {
    SnapshotOp.CREATE: "create snapshot",
    SnapshotOp.MKDIR: "create snapshot directory",
    SnapshotOp.MAKE_WRITABLE: "make snapshot writable",
    SnapshotOp.MAKE_READONLY: "make snapshot read-only",
    SnapshotOp.DELETE: "delete snapshot",
    SnapshotOp.LIST: "list snapshots",
    SnapshotOp.READ_LATEST: "read latest symlink",
    SnapshotOp.UPDATE_LATEST: "update latest symlink",
}

_SKIP_REASONS: dict[SnapshotSkipReason, str] = {
    SnapshotSkipReason.NO_SNAPSHOTS: "no snapshots configured",
    SnapshotSkipReason.NO_MAX_SNAPSHOTS: "no max-snapshots limit",
    SnapshotSkipReason.INACTIVE: "inactive",
    SnapshotSkipReason.PREFLIGHT_ERRORS: "preflight errors ignored",
}


def format_operation(op: SnapshotOp) -> str:
    """Return the human phrase for a snapshot operation, e.g. ``delete snapshot``."""
    return _OPERATIONS[op]


def format_skip_reason(reason: SnapshotSkipReason) -> str:
    """Return the human phrase for why a sync was skipped."""
    return _SKIP_REASONS[reason]


def snapshot_error_text(error: SnapshotResultError) -> str:
    """Return a one-line description of a sync's snapshot failure."""
    errors = format_problems(error.preflight_errors)
    match error.kind:
        case SnapshotErrorKind.INACTIVE:
            return f"inactive: {errors}"
        case SnapshotErrorKind.PREFLIGHT_ERRORS:
            return f"preflight errors: {errors}"
        case SnapshotErrorKind.OPERATION_FAILED:
            operation = (
                format_operation(error.operation) if error.operation else "operation"
            )
            return f"{operation} failed for {error.path}: {error.message}"


def _status_text(
    skip_reason: SnapshotSkipReason | None,
    error: SnapshotResultError | None,
) -> Text:
    """
    Format a sync's status as text, not markup.

    The error carries remote stderr, which may contain square brackets.
    """
    match (skip_reason, error):
        case (_, SnapshotResultError() as e):
            return Text(f"FAILED ({snapshot_error_text(e)})", style="red")
        case (SnapshotSkipReason() as reason, None):
            return Text(f"SKIPPED ({format_skip_reason(reason)})", style="dim")
        case _:
            return Text("OK", style="green")


def format_human_prune_results(results: list[PruneResult], *, dry_run: bool) -> Table:
    """Return a table of each sync's deleted and kept snapshot counts and status."""
    table = Table(
        title=dry_run_table_title("NBKP prune:", dry_run=dry_run),
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

    return table


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


def format_human_show_results(results: list[ShowResult]) -> Table:
    """Return a table of each sync's snapshot mode, count, latest and retention."""
    table = Table(title="Snapshots:")
    table.add_column("Name", style="bold")
    table.add_column("Mode")
    table.add_column("Snapshots")
    table.add_column("Latest")
    table.add_column("Retention")
    table.add_column("Status")

    for r in results:
        table.add_row(*_show_row(r))

    return table
