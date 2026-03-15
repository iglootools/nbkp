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
from ..config.epresolution import ResolvedEndpoints
from ..config.output import endpoint_path
from ..fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from ..preflight.status import SyncStatus
from .rsync import build_rsync_command
from .runner import PruneResult, SyncOutcome, SyncResult


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


def _retention_display(max_snapshots: int | None) -> str:
    """Format a max_snapshots value for display."""
    if max_snapshots is None:
        return "unlimited"
    else:
        return f"keep {max_snapshots}"


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
        dst_ep = config.destination_endpoint(ss.config)
        dest_suffix: str | None = None
        link_dest: str | None = None
        match dst_ep.snapshot_mode:
            case "btrfs":
                dest_suffix = STAGING_DIR
            case "hard-link":
                dest_suffix = f"{SNAPSHOTS_DIR}/<timestamp>"
                if ss.destination_latest_snapshot:
                    link_dest = f"../{ss.destination_latest_snapshot.name}"
        cmd = build_rsync_command(
            ss.config,
            config,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=dest_suffix,
            link_dest=link_dest,
        )
        table.add_row(ss.slug, shlex.join(cmd))

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
        table.add_row(slug, mode, "\n".join(cmds), retention)

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
