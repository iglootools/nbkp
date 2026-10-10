"""Sync run/prune output formatting."""

from __future__ import annotations

import shlex

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
)
from ..config.output import endpoint_path
from ..fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from ..preflight.status import SyncStatus
from ..remote.endpoints import ResolvedEndpoints
from ..snapshots.output import retention_display as _retention_display
from .results import SyncOutcome, SyncResult
from .rsync import build_rsync_command

# ---------------------------------------------------------------------------
# Run preview (rsync commands + snapshot commands)
# ---------------------------------------------------------------------------


def _resolve_dest_display_path(
    sync_status: SyncStatus,
    config: Config,
) -> str:
    """Resolve the destination display path for a sync."""
    dst_ep = config.destination_endpoint(sync_status.config)
    vol = config.volumes[dst_ep.volume]
    return endpoint_path(vol, dst_ep.subdir)


def _snapshot_preview_commands(
    sync_status: SyncStatus,
    config: Config,
) -> tuple[str, list[str], str] | None:
    """Build snapshot preview commands for an active sync.

    Returns ``(mode, command_lines, retention)`` or ``None`` if
    the sync has no snapshot configuration.
    """
    dst_ep = config.destination_endpoint(sync_status.config)
    dest_path = _resolve_dest_display_path(sync_status, config)
    match dst_ep.snapshot_mode:
        case "btrfs":
            cmds = [
                f"btrfs subvolume snapshot -r {dest_path}/{STAGING_DIR}/ {dest_path}/{SNAPSHOTS_DIR}/<timestamp>",
                f"ln -sfn {SNAPSHOTS_DIR}/<timestamp> {dest_path}/{LATEST_LINK}",
            ]
            return (
                "btrfs",
                cmds,
                _retention_display(dst_ep.btrfs_snapshots.max_snapshots),
            )
        case "hard-link":
            cmds = [
                f"mkdir -p {dest_path}/{SNAPSHOTS_DIR}/<timestamp>",
                f"ln -sfn {SNAPSHOTS_DIR}/<timestamp> {dest_path}/{LATEST_LINK}",
            ]
            return (
                "hard-link",
                cmds,
                _retention_display(dst_ep.hard_link_snapshots.max_snapshots),
            )
        case "none":
            return None


def _preview_rsync_target(
    ss: SyncStatus, config: Config
) -> tuple[str | None, str | None]:
    """``(dest_suffix, link_dest)`` that ``run`` would pass to rsync."""
    match config.destination_endpoint(ss.config).snapshot_mode:
        case "btrfs":
            return (STAGING_DIR, None)
        case "hard-link":
            latest = ss.destination_latest_snapshot
            return (
                f"{SNAPSHOTS_DIR}/<timestamp>",
                f"../{latest.name}" if latest else None,
            )
        case _:
            return (None, None)


def _build_rsync_commands_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build the Rsync Commands table section."""
    active_syncs = [ss for ss in sync_statuses.values() if ss.active]
    if not active_syncs:
        return []
    table = Table(title="Rsync Commands:")
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
    """Build the Snapshot Commands table section."""
    rows = [
        (ss.slug, preview)
        for ss in sync_statuses.values()
        if ss.active
        for preview in [_snapshot_preview_commands(ss, config)]
        if preview is not None
    ]
    if not rows:
        return []
    table = Table(title="Snapshot Commands:")
    table.add_column("Sync", style="bold")
    table.add_column("Mode")
    table.add_column("Post-rsync")
    table.add_column("Retention")

    for slug, (mode, cmds, retention) in rows:
        table.add_row(slug, mode, Text("\n".join(cmds)), retention)

    return [Text(""), table]


def build_run_preview_sections(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build renderable sections for run preview output."""
    return [
        *_build_rsync_commands_section(sync_statuses, config, resolved_endpoints),
        *_build_snapshot_commands_section(sync_statuses, config),
    ]


def print_run_preview(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    wrap_in_panel: bool = True,
) -> None:
    """Print human-readable run preview (rsync + snapshot commands)."""
    re = resolved_endpoints or {}
    c = console or Console()

    sections = build_run_preview_sections(sync_statuses, config, re)

    if wrap_in_panel:
        c.print(
            Panel(
                Group(*sections),
                title="[bold]Run Preview[/bold]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    else:
        for section in sections:
            c.print(section)


# ---------------------------------------------------------------------------
# Run results
# ---------------------------------------------------------------------------


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


def _result_details(
    r: SyncResult,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[str]:
    """Detail lines of one result: error, snapshot, pruning, warnings, output."""
    return [
        *([f"Error: {r.detail}"] if r.detail else []),
        *(
            [
                "Snapshot: "
                + _format_snapshot_display(
                    r.snapshot_path, r.sync_slug, config, resolved_endpoints
                )
            ]
            if r.snapshot_path
            else []
        ),
        *([f"Pruned: {len(r.pruned_paths)} snapshot(s)"] if r.pruned_paths else []),
        *(f"Warning: {w.message}" for w in r.warnings),
        *(r.output.strip().split("\n")[:5] if r.output and not r.success else []),
    ]


def build_human_results_sections(
    results: list[SyncResult],
    dry_run: bool,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build renderable sections for run results output."""
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"Sync results{mode}:",
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
    for section in build_human_results_sections(
        results, dry_run, config, resolved_endpoints
    ):
        c.print(section)
