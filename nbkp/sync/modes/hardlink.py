"""Hard-link snapshot sync: rsync into a new snapshot dir with ``--link-dest``."""

from __future__ import annotations

from ...config import SyncConfig, SyncEndpoint
from ...fsprotocol import SNAPSHOTS_DIR, Snapshot
from ...preflight.status.sync import SyncStatus
from ...snapshots.common import destination_volume
from ...snapshots.errors import SnapshotOperationError
from ...snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot as hl_delete_snapshot,
    prune_snapshots as hl_prune_snapshots,
    snapshot_path_for,
)
from ..results import SyncFailureKind, SyncResult, SyncWarning, SyncWarningKind
from .common import RunContext, publish_snapshot, rsync_step


def run_hard_link_sync(
    status: SyncStatus, dst: SyncEndpoint, ctx: RunContext
) -> SyncResult:
    """Run a sync with hard-link snapshot strategy.

    A dry run never modifies the destination: no orphan cleanup, and
    rsync previews a transfer into a snapshot directory that is never
    created.
    """
    sync = status.config
    warnings = () if ctx.dry_run else _cleanup_orphans(sync, ctx)
    latest = status.destination_latest_snapshot
    link_dest = f"../{latest.name}" if latest else None
    match _prepare_snapshot_dir(sync, ctx):
        case SyncResult() as failure:
            return failure.with_warnings(warnings)
        case snapshot_path:
            return _rsync_into_snapshot(
                sync, dst, snapshot_path, link_dest, warnings, ctx
            )


def _cleanup_orphans(sync: SyncConfig, ctx: RunContext) -> tuple[SyncWarning, ...]:
    """Remove snapshots left by failed syncs (best-effort: a warning)."""
    try:
        cleanup_orphaned_snapshots(
            sync, ctx.config, resolved_endpoints=ctx.resolved_endpoints
        )
        return ()
    except SnapshotOperationError as e:
        return (
            SyncWarning(
                kind=SyncWarningKind.ORPHAN_CLEANUP,
                message=f"Orphaned snapshot cleanup failed: {e}",
            ),
        )


def _prepare_snapshot_dir(sync: SyncConfig, ctx: RunContext) -> str | SyncResult:
    """The new snapshot's path; created on disk unless dry-running."""
    now = ctx.clock()
    if ctx.dry_run:
        return snapshot_path_for(sync, ctx.config, now=now, platform=ctx.platform)
    try:
        return create_snapshot_dir(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
        )
    except SnapshotOperationError as e:
        return SyncResult.failed(
            sync.slug,
            ctx.dry_run,
            SyncFailureKind.MKDIR,
            f"Failed to create snapshot dir: {e}",
        )


def _rsync_into_snapshot(
    sync: SyncConfig,
    dst: SyncEndpoint,
    snapshot_path: str,
    link_dest: str | None,
    warnings: tuple[SyncWarning, ...],
    ctx: RunContext,
) -> SyncResult:
    snapshot = Snapshot.from_path(snapshot_path)
    suffix = f"{SNAPSHOTS_DIR}/{snapshot.name}"
    match rsync_step(sync, ctx, dest_suffix=suffix, link_dest=link_dest):
        case SyncResult() as failure:
            discard = _discard_snapshot_dir(snapshot_path, sync, ctx)
            return failure.with_warnings((*warnings, *discard))
        case proc if ctx.dry_run:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc, warnings=warnings)
        case proc:
            result = publish_snapshot(
                sync,
                proc,
                snapshot_path,
                dst.hard_link_snapshots.max_snapshots,
                hl_prune_snapshots,
                ctx,
            )
            return result.with_warnings(warnings)


def _discard_snapshot_dir(
    snapshot_path: str, sync: SyncConfig, ctx: RunContext
) -> tuple[SyncWarning, ...]:
    """Remove the new snapshot dir after a failed rsync (best-effort).

    A leftover is harmless — the next run's orphan cleanup removes it — so
    a failure is a warning.  Dry runs never created the directory.
    """
    if ctx.dry_run:
        return ()
    try:
        hl_delete_snapshot(
            snapshot_path,
            destination_volume(sync, ctx.config),
            ctx.resolved_endpoints,
        )
        return ()
    except SnapshotOperationError as e:
        return (
            SyncWarning(
                kind=SyncWarningKind.SNAPSHOT_DIR_CLEANUP,
                message=f"Failed to remove snapshot dir after rsync failure: {e}",
            ),
        )
