"""Hard-link snapshot sync: rsync into a new snapshot dir with ``--link-dest``."""

from nbkp.config import SyncConfig, SyncEndpoint
from nbkp.fsprotocol import Snapshot
from nbkp.preflight import SyncStatus
from nbkp.snapshots.common import destination_volume, ensure_after_latest
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot as hl_delete_snapshot,
    prune_snapshots as hl_prune_snapshots,
    snapshot_path_for,
)
from nbkp.sync.modes.common import (
    RunContext,
    publish_snapshot,
    rsync_step,
    rsync_succeeded,
)
from nbkp.sync.results import SyncFailureKind, SyncResult, SyncWarning, SyncWarningKind
from nbkp.sync.rsync import hard_link_dest_suffix, hard_link_link_dest


def run_hard_link_sync(
    status: SyncStatus,
    dst: SyncEndpoint,
    ctx: RunContext,
) -> SyncResult:
    """
    Run a sync into a new hard-link snapshot.

    A dry run never modifies the destination: no orphan cleanup, and rsync previews a
    transfer into a snapshot directory that is never created.
    """
    sync = status.config
    match () if ctx.dry_run else _cleanup_orphans(sync, ctx):
        case SyncResult() as failure:
            return failure
        case warnings:
            return _sync_into_new_snapshot(status, dst, warnings, ctx)


def _sync_into_new_snapshot(
    status: SyncStatus,
    dst: SyncEndpoint,
    warnings: tuple[SyncWarning, ...],
    ctx: RunContext,
) -> SyncResult:
    sync = status.config
    latest = status.destination_latest_snapshot
    link_dest = hard_link_link_dest(latest.name if latest else None)
    match _prepare_snapshot_dir(sync, latest, ctx):
        case SyncResult() as failure:
            return failure.with_warnings(warnings)
        case snapshot_path:
            return _rsync_into_snapshot(
                sync,
                dst,
                snapshot_path,
                link_dest,
                warnings,
                ctx=ctx,
            )


def _cleanup_orphans(
    sync: SyncConfig,
    ctx: RunContext,
) -> tuple[SyncWarning, ...] | SyncResult:
    """
    Remove the snapshots left by failed syncs.

    An orphan that cannot be deleted fails the sync before a new snapshot is made:
    the new one would move ``latest`` past the orphan, which orphan cleanup then
    never looks at again, and retention and ``snapshots show`` would count the
    incomplete tree as a backup. When orphans cannot be looked for (``latest`` absent,
    a listing that failed), nothing was deleted, and the sync goes on with a warning.
    """
    try:
        cleanup_orphaned_snapshots(
            sync,
            ctx.config,
            resolved_endpoints=ctx.resolved_endpoints,
            run=ctx.run_command,
        )
    except SnapshotOperationError as e:
        match e.op:
            case SnapshotOp.DELETE:
                return SyncResult.failed(
                    sync.slug,
                    dry_run=ctx.dry_run,
                    failure=SyncFailureKind.ORPHAN_CLEANUP,
                    detail=f"Failed to remove an orphaned snapshot: {e}",
                )
            case _:
                return (SyncWarning.snapshot_failed(SyncWarningKind.ORPHAN_CLEANUP, e),)
    else:
        return ()


def _prepare_snapshot_dir(
    sync: SyncConfig,
    latest: Snapshot | None,
    ctx: RunContext,
) -> str | SyncResult:
    """
    Return the new snapshot's path, created on disk unless dry-running.

    A snapshot that would not come after *latest* is refused (see
    :func:`~nbkp.snapshots.common.ensure_after_latest`).
    """
    now = ctx.clock()
    if ctx.dry_run:
        return snapshot_path_for(sync, ctx.config, now=now, platform=ctx.platform)
    try:
        ensure_after_latest(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            latest=latest,
            op=SnapshotOp.MKDIR,
        )
        return create_snapshot_dir(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
            run=ctx.run_command,
        )
    except SnapshotOperationError as e:
        return SyncResult.failed(
            sync.slug,
            dry_run=ctx.dry_run,
            failure=SyncFailureKind.MKDIR,
            detail=f"Failed to create snapshot dir: {e}",
        )


def _rsync_into_snapshot(
    sync: SyncConfig,
    dst: SyncEndpoint,
    snapshot_path: str,
    link_dest: str | None,
    warnings: tuple[SyncWarning, ...],
    *,
    ctx: RunContext,
) -> SyncResult:
    suffix = hard_link_dest_suffix(Snapshot.from_path(snapshot_path).name)
    match rsync_step(sync, ctx, dest_suffix=suffix, link_dest=link_dest):
        case SyncResult() as failure:
            discard = _discard_snapshot_dir(snapshot_path, sync, ctx)
            return failure.with_warnings((*warnings, *discard))
        case proc if ctx.dry_run:
            return rsync_succeeded(sync, proc, ctx, warnings=warnings)
        case proc:
            result = publish_snapshot(
                sync,
                proc,
                snapshot_path,
                dst.hard_link_snapshots.max_snapshots,
                hl_prune_snapshots,
                ctx=ctx,
            )
            return result.with_warnings(warnings)


def _discard_snapshot_dir(
    snapshot_path: str,
    sync: SyncConfig,
    ctx: RunContext,
) -> tuple[SyncWarning, ...]:
    """
    Remove a snapshot directory a failed sync left, warning when that fails.

    A leftover is harmless: the next run's orphan cleanup removes it, or fails that
    run before ``latest`` can move past it. So a failure here is a warning. Dry runs
    never created the directory.
    """
    if ctx.dry_run:
        return ()
    try:
        hl_delete_snapshot(
            snapshot_path,
            destination_volume(sync, ctx.config),
            ctx.resolved_endpoints,
            run=ctx.run_command,
        )
    except SnapshotOperationError as e:
        return (SyncWarning.snapshot_failed(SyncWarningKind.SNAPSHOT_DIR_CLEANUP, e),)
    else:
        return ()
