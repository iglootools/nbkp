"""Rich rendering of syncs: the commands ``run --dry-run`` previews, and its results."""

import shlex

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from nbkp.clidisplay import dry_run_table_title
from nbkp.config import Config, LocalVolume, RemoteVolume
from nbkp.config.output import endpoint_path
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from nbkp.preflight import SyncStatus
from nbkp.preflight.output import format_problems
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.output import retention_display as _retention_display
from nbkp.sync.results import SyncOutcome, SyncResult
from nbkp.sync.rsync import (
    build_rsync_command,
    hard_link_dest_suffix,
    hard_link_link_dest,
)


def _resolve_dest_display_path(
    sync_status: SyncStatus,
    config: Config,
) -> str:
    dst_ep = config.destination_endpoint(sync_status.config)
    vol = config.volumes[dst_ep.volume]
    return endpoint_path(vol, dst_ep.subdir)


def _snapshot_preview_commands(
    sync_status: SyncStatus,
    config: Config,
) -> tuple[str, list[str], str] | None:
    """``(mode, command_lines, retention)``, or ``None`` without snapshots."""
    dst_ep = config.destination_endpoint(sync_status.config)
    dest_path = _resolve_dest_display_path(sync_status, config)
    match dst_ep.snapshot_mode:
        case "btrfs":
            cmds = [
                (
                    f"btrfs subvolume snapshot -r {dest_path}/{STAGING_DIR}/"
                    f" {dest_path}/{SNAPSHOTS_DIR}/<timestamp>"
                ),
                f"ln -sfn {SNAPSHOTS_DIR}/<timestamp> {dest_path}/{LATEST_LINK}",
            ]
            return (
                "btrfs",
                cmds,
                _retention_display(dst_ep.max_snapshots),
            )
        case "hard-link":
            cmds = [
                # Plain mkdir, as create_snapshot_dir runs it: the run fails
                # when the snapshot directory already exists.
                f"mkdir {dest_path}/{SNAPSHOTS_DIR}/<timestamp>",
                f"ln -sfn {SNAPSHOTS_DIR}/<timestamp> {dest_path}/{LATEST_LINK}",
            ]
            return (
                "hard-link",
                cmds,
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
    active_syncs = [ss for ss in sync_statuses.values() if ss.active]
    if not active_syncs:
        return []
    table = Table(title=dry_run_table_title("Rsync commands:", dry_run=True))
    table.add_column("Sync", style="bold")
    table.add_column("Command")

    for ss in active_syncs:
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
        if ss.active
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
    Return the tables of the commands ``run`` would execute for the active syncs.

    The rsync commands, then the snapshot commands; a table with no row is left out.
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
        *(f"Warning: {w.message}" for w in r.warnings),
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
