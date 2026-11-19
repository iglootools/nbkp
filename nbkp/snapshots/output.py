from rich.table import Table
from rich.text import Text

from .models import (
    PruneResult,
    ShowResult,
    SnapshotErrorKind,
    SnapshotResultError,
    SnapshotSkipReason,
)


def retention_display(max_snapshots: int | None) -> str:
    if max_snapshots is None:
        return "unlimited"
    else:
        return f"keep {max_snapshots}"


def snapshot_error_text(error: SnapshotResultError) -> str:
    errors = ", ".join(e.value for e in error.preflight_errors)
    match error.kind:
        case SnapshotErrorKind.INACTIVE:
            return f"inactive: {errors}"
        case SnapshotErrorKind.PREFLIGHT_ERRORS:
            return f"preflight errors: {errors}"
        case SnapshotErrorKind.OPERATION_FAILED:
            operation = error.operation.value if error.operation else "operation"
            return f"{operation} failed for {error.path}: {error.message}"


def _status_text(
    skip_reason: SnapshotSkipReason | None, error: SnapshotResultError | None
) -> Text:
    """Text, not markup: the error carries remote stderr, which may contain
    square brackets.
    """
    match (skip_reason, error):
        case (_, SnapshotResultError() as e):
            return Text(f"FAILED ({snapshot_error_text(e)})", style="red")
        case (SnapshotSkipReason() as reason, None):
            return Text(f"SKIPPED ({reason.value})", style="dim")
        case _:
            return Text("OK", style="green")


def format_human_prune_results(results: list[PruneResult], dry_run: bool) -> Table:
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
