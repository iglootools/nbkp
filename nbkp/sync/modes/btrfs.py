"""Btrfs snapshot sync: rsync into ``staging/``, then a read-only snapshot."""

import subprocess

from nbkp.config import SyncConfig, SyncEndpoint
from nbkp.fsprotocol import STAGING_DIR, Snapshot
from nbkp.preflight import SyncStatus
from nbkp.snapshots.btrfs import (
    create_snapshot,
    prune_snapshots as btrfs_prune_snapshots,
)
from nbkp.snapshots.common import ensure_after_latest
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.sync.modes.common import (
    RunContext,
    failed_after_rsync,
    publish_snapshot,
    rsync_step,
    rsync_succeeded,
)
from nbkp.sync.results import SyncFailureKind, SyncResult


def run_btrfs_sync(
    status: SyncStatus,
    dst: SyncEndpoint,
    ctx: RunContext,
) -> SyncResult:
    """
    Rsync into ``staging/``, snapshot it read-only, and return the sync's result.

    A dry run stops after rsync. The snapshot is not taken when it would not
    come after the destination's latest one (a ``SNAPSHOT`` failure).
    """
    sync = status.config
    match rsync_step(sync, ctx, dest_suffix=STAGING_DIR):
        case SyncResult() as failure:
            return failure
        case proc if ctx.dry_run:
            return rsync_succeeded(sync, proc, ctx)
        case proc:
            latest = status.destination_latest_snapshot
            return _btrfs_post_rsync(sync, dst, latest, proc, ctx)


def _btrfs_post_rsync(
    sync: SyncConfig,
    dst: SyncEndpoint,
    latest: Snapshot | None,
    proc: subprocess.CompletedProcess[str],
    ctx: RunContext,
) -> SyncResult:
    """
    Snapshot ``staging/``, unless the snapshot would not come after *latest*.

    See :func:`~nbkp.snapshots.common.ensure_after_latest`.
    """
    now = ctx.clock()
    try:
        ensure_after_latest(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            latest=latest,
            op=SnapshotOp.CREATE,
        )
        snapshot_path = create_snapshot(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
            run=ctx.run_command,
        )
    except SnapshotOperationError as e:
        return failed_after_rsync(
            sync,
            proc,
            ctx,
            SyncFailureKind.SNAPSHOT,
            f"Snapshot failed: {e}",
        )
    return publish_snapshot(
        sync,
        proc,
        snapshot_path,
        dst.btrfs_snapshots.max_snapshots,
        btrfs_prune_snapshots,
        ctx=ctx,
    )
