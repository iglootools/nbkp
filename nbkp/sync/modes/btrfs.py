"""Btrfs snapshot sync: rsync into ``staging/``, then a read-only snapshot."""

import subprocess

from ...config import SyncConfig, SyncEndpoint
from ...fsprotocol import STAGING_DIR, Snapshot
from ...preflight.status.sync import SyncStatus
from ...snapshots.btrfs import (
    create_snapshot,
    prune_snapshots as btrfs_prune_snapshots,
)
from ...snapshots.common import ensure_after_latest
from ...snapshots.errors import SnapshotOp, SnapshotOperationError
from ..results import SyncFailureKind, SyncResult
from .common import RunContext, failed_after_rsync, publish_snapshot, rsync_step


def run_btrfs_sync(
    status: SyncStatus, dst: SyncEndpoint, ctx: RunContext
) -> SyncResult:
    sync = status.config
    match rsync_step(sync, ctx, dest_suffix=STAGING_DIR):
        case SyncResult() as failure:
            return failure
        case proc if ctx.dry_run:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)
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
    """Snapshot ``staging/``, unless the snapshot would not come after
    *latest* (see :func:`~nbkp.snapshots.common.ensure_after_latest`).
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
            sync, proc, ctx, SyncFailureKind.SNAPSHOT, f"Snapshot failed: {e}"
        )
    return publish_snapshot(
        sync,
        proc,
        snapshot_path,
        dst.btrfs_snapshots.max_snapshots,
        btrfs_prune_snapshots,
        ctx,
    )
