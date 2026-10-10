"""Btrfs snapshot sync: rsync into ``staging/``, then a read-only snapshot."""

from __future__ import annotations

import subprocess

from ...config import SyncConfig, SyncEndpoint
from ...fsprotocol import STAGING_DIR
from ...snapshots.btrfs import (
    create_snapshot,
    prune_snapshots as btrfs_prune_snapshots,
)
from ...snapshots.errors import SnapshotOperationError
from ..results import SyncFailureKind, SyncResult
from .common import RunContext, failed_after_rsync, publish_snapshot, rsync_step


def run_btrfs_sync(sync: SyncConfig, dst: SyncEndpoint, ctx: RunContext) -> SyncResult:
    """Run a sync with btrfs snapshot strategy (rsync into ``staging/``)."""
    match rsync_step(sync, ctx, dest_suffix=STAGING_DIR):
        case SyncResult() as failure:
            return failure
        case proc if ctx.dry_run:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)
        case proc:
            return _btrfs_post_rsync(sync, dst, proc, ctx)


def _btrfs_post_rsync(
    sync: SyncConfig,
    dst: SyncEndpoint,
    proc: subprocess.CompletedProcess[str],
    ctx: RunContext,
) -> SyncResult:
    """Snapshot ``staging/``, update ``latest``, and prune."""
    try:
        snapshot_path = create_snapshot(
            sync,
            ctx.config,
            now=ctx.clock(),
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
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
