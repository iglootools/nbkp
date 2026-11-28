"""Rich rendering of syncs: the commands ``run --dry-run`` previews, and its results."""

import shlex

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from nbkp.config import Config, LocalVolume, RemoteVolume
from nbkp.config.output import endpoint_path
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from nbkp.preflight import SyncStatus
from nbkp.preflight.output import format_problems
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.btrfs import build_create_snapshot_args
from nbkp.snapshots.common import build_update_latest_args
from nbkp.snapshots.hardlinks import build_mkdir_snapshot_args
from nbkp.snapshots.output import retention_display as _retention_display
from nbkp.sync.results import SyncOutcome, SyncResult, SyncWarning, SyncWarningKind
from nbkp.sync.rsync import (
    build_rsync_command,
    hard_link_dest_suffix,
    hard_link_link_dest,
)
from nbkp.theme import dry_run_table_title


def _resolve_dest_display_path(
    sync_status: SyncStatus,
    config: Config,
) -> str:
    dst_ep = config.destination_endpoint(sync_status.config)
    vol = config.volumes[dst_ep.volume]
    return endpoint_path(vol, dst_ep.subdir)


_PREVIEW_SNAPSHOT = f"{SNAPSHOTS_DIR}/<timestamp>"


def _snapshot_preview_commands(
    sync_status: SyncStatus,
    config: Config,
) -> tuple[str, list[str], str] | None:
    """
    ``(mode, command_lines, retention)``, or ``None`` without snapshots.

    Built with the backends' own command builders, so the preview cannot drift
    from what the run executes, and joined with ``shlex.join`` so a path with a
    space copies as one word.
    """
    dst_ep = config.destination_endpoint(sync_status.config)
    dest_path = _resolve_dest_display_path(sync_status, config)
    snapshot_path = f"{dest_path}/{_PREVIEW_SNAPSHOT}"
    update_latest = build_update_latest_args(
        _PREVIEW_SNAPSHOT,
        f"{dest_path}/{LATEST_LINK}",
    )
    match dst_ep.snapshot_mode:
        case "btrfs":
            create = build_create_snapshot_args(
                f"{dest_path}/{STAGING_DIR}",
                snapshot_path,
            )
            return (
                "btrfs",
                [shlex.join(create), shlex.join(update_latest)],
                _retention_display(dst_ep.max_snapshots),
            )
        case "hard-link":
            mkdir = build_mkdir_snapshot_args(snapshot_path)
            return (
                "hard-link",
                [shlex.join(mkdir), shlex.join(update_latest)],
                _retention_display(dst_ep.max_snapshots),
            )
        case "none":
            return None


def _preview_rsync_target(
    ss: SyncStatus,
    config: Config,
) -> tuple[str | None, str | None]:
    """``(dest_suffix, link_dest)`` that ``run`` would pass to rsync."""
    match config.destination_endpoint(ss.config).snapshot_mode:
        case "btrfs":
            return (STAGING_DIR, None)
        case "hard-link":
            latest = ss.destination_latest_snapshot
            return (
                hard_link_dest_suffix("<timestamp>"),
                hard_link_link_dest(latest.name if latest else None),
            )
        case _:
            return (None, None)


def _build_rsync_commands_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    if not sync_statuses:
        return []
    table = Table(title=dry_run_table_title("Rsync commands:", dry_run=True))
    table.add_column("Sync", style="bold")
    table.add_column("Command")

    for ss in sync_statuses.values():
        dest_suffix, link_dest = _preview_rsync_target(ss, config)
        cmd = build_rsync_command(
            ss.config,
            config,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=dest_suffix,
            link_dest=link_dest,
        )
        # Text, not str: rsync filter rules and paths may contain "[...]",
        # which Rich would parse as a style tag and drop from the preview.
        table.add_row(ss.slug, Text(shlex.join(cmd)))

    return [Text(""), table]


def _build_snapshot_commands_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
) -> list[RenderableType]:
    rows = [
        (ss.slug, preview)
        for ss in sync_statuses.values()
        for preview in [_snapshot_preview_commands(ss, config)]
        if preview is not None
    ]
    if not rows:
        return []
    table = Table(title=dry_run_table_title("Snapshot commands:", dry_run=True))
    table.add_column("Sync", style="bold")
    table.add_column("Mode")
    table.add_column("Commands")
    table.add_column("Retention")

    for slug, (mode, cmds, retention) in rows:
        table.add_row(slug, mode, Text("\n".join(cmds)), retention)

    return [Text(""), table]


def build_run_preview_sections(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """
    Return the tables of the commands ``run`` would execute for *sync_statuses*.

    The caller passes the syncs the run attempts, which ``ignore-all`` widens
    beyond the active ones (see ``PipelineResult.attempted_sync_statuses``). The
    rsync commands, then the snapshot commands; a table with no row is left out.
    Shown by ``run --dry-run``, so each title carries the dry-run label.
    """
    return [
        *_build_rsync_commands_section(sync_statuses, config, resolved_endpoints),
        *_build_snapshot_commands_section(sync_statuses, config),
    ]


def _format_snapshot_display(
    snapshot_path: str,
    sync_slug: str,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
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
    match outcome:
        case SyncOutcome.SUCCESS:
            return Text("OK", style="green")
        case SyncOutcome.CANCELLED:
            return Text("CANCELLED", style="yellow")
        case SyncOutcome.SKIPPED:
            return Text("SKIPPED", style="dim")
        case SyncOutcome.FAILED:
            return Text("FAILED", style="red")


# The last lines: rsync prints its error at the end, after any progress lines.
_FAILED_OUTPUT_LINES = 5


def _skip_reason(r: SyncResult) -> str:
    return f"Error: Sync not active: {format_problems(r.preflight_errors) or 'unknown'}"


def _result_details(
    r: SyncResult,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[str]:
    return [
        *([f"Error: {r.detail}"] if r.detail else []),
        *([_skip_reason(r)] if r.outcome is SyncOutcome.SKIPPED else []),
        *(
            [
                "Snapshot: "
                + _format_snapshot_display(
                    r.snapshot_path,
                    r.sync_slug,
                    config,
                    resolved_endpoints,
                ),
            ]
            if r.snapshot_path
            else []
        ),
        *([f"Pruned: {len(r.pruned_paths)} snapshot(s)"] if r.pruned_paths else []),
        *(f"Warning: {sync_warning_message(w)}" for w in r.warnings),
        *(
            r.output.strip().split("\n")[-_FAILED_OUTPUT_LINES:]
            if r.output and not r.success
            else []
        ),
    ]


def build_human_results_sections(
    results: list[SyncResult],
    *,
    dry_run: bool,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """
    Return the table of sync results: each sync's status and details.

    The details are the error, the skip reason, the snapshot taken, the snapshots
    pruned, the warnings, and the last lines of rsync's output for a failed sync.
    """
    table = Table(
        title=dry_run_table_title("Sync results:", dry_run=dry_run),
    )
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for r in results:
        # Text, not str: details carry rsync's own output, which uses square
        # brackets liberally ("rsync error: ... [sender=3.4.1]") and would be
        # silently eaten by Rich's markup parser.
        table.add_row(
            r.sync_slug,
            _outcome_text(r.outcome),
            Text("\n".join(_result_details(r, config, resolved_endpoints))),
        )

    return [table]


def sync_warning_message(warning: SyncWarning) -> str:
    """Render the sentence that says what went wrong without failing the sync."""
    match warning.kind:
        case SyncWarningKind.ORPHAN_CLEANUP:
            return f"Orphaned snapshot cleanup failed: {_snapshot_failure(warning)}"
        case SyncWarningKind.PRUNE:
            return f"Prune failed: {_snapshot_failure(warning)}"
        case SyncWarningKind.SNAPSHOT_DIR_CLEANUP:
            return (
                "Failed to remove snapshot dir after rsync failure:"
                f" {_snapshot_failure(warning)}"
            )
        case SyncWarningKind.VANISHED_FILES:
            return (
                "Some files vanished from the source during the transfer"
                f" (rsync exit code {warning.exit_code}); everything else was copied"
            )


def _snapshot_failure(warning: SyncWarning) -> str:
    return f"{warning.op} failed for {warning.path}: {warning.stderr}"


def sync_result_json(result: SyncResult) -> dict[str, object]:
    """
    Return a sync result for ``-o json``.

    Each warning keeps its kind and facts, and gains its rendered ``message``.
    """
    return {
        **result.model_dump(mode="json"),
        "warnings": [
            {
                **w.model_dump(mode="json", exclude_none=True),
                "message": sync_warning_message(w),
            }
            for w in result.warnings
        ],
    }
