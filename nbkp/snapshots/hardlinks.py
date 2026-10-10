"""Hard-link snapshot creation, lookup, symlink management, and pruning."""

from __future__ import annotations

import shutil
from datetime import datetime

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    Volume,
)
from ..remote.endpoints import ResolvedEndpoints
from .common import (
    create_snapshot_timestamp,
    destination_volume,
    list_snapshots,
    read_latest_symlink,
    snapshots_dir,
)
from .errors import SnapshotOp, local_fs_op, run_checked


def create_snapshot_dir(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """Create a snapshot directory for the current sync.

    *now* names the snapshot and *platform* (``sys.platform``) decides the
    macOS-safe name form; both come from the caller.  Returns the full
    snapshot path.
    """
    re = resolved_endpoints or {}
    dst_vol = destination_volume(sync, config)
    snapshot = create_snapshot_timestamp(now, dst_vol, platform)
    snapshot_path = f"{snapshots_dir(sync, config)}/{snapshot.name}"
    run_checked(
        SnapshotOp.MKDIR, snapshot_path, ["mkdir", "-p", snapshot_path], dst_vol, re
    )
    return snapshot_path


def snapshot_path_for(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
) -> str:
    """The path :func:`create_snapshot_dir` would create, without creating it.

    Used by dry runs, which must not modify the destination.
    """
    dst_vol = destination_volume(sync, config)
    snapshot = create_snapshot_timestamp(now, dst_vol, platform)
    return f"{snapshots_dir(sync, config)}/{snapshot.name}"


def cleanup_orphaned_snapshots(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[str]:
    """Remove snapshots newer than the latest symlink target.

    These are leftover directories from failed syncs.
    Returns list of deleted paths.
    """
    re = resolved_endpoints or {}
    latest = read_latest_symlink(sync, config, resolved_endpoints=re)
    if latest is None:
        return []
    else:
        base = snapshots_dir(sync, config)
        orphans = [
            f"{base}/{s.name}"
            for s in list_snapshots(sync, config, re)
            if s.timestamp > latest.timestamp
        ]
        dst_vol = destination_volume(sync, config)
        for path in orphans:
            delete_snapshot(path, dst_vol, re)
        return orphans


def delete_snapshot(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Delete a hard-link snapshot directory."""
    match volume:
        case RemoteVolume():
            run_checked(
                SnapshotOp.DELETE, path, ["rm", "-rf", path], volume, resolved_endpoints
            )
        case LocalVolume():
            local_fs_op(SnapshotOp.DELETE, path, lambda: shutil.rmtree(path))


def prune_snapshots(
    sync: SyncConfig,
    config: Config,
    max_snapshots: int,
    *,
    dry_run: bool = False,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[str]:
    """Delete oldest snapshots exceeding max_snapshots.

    Never prunes the snapshot that the latest symlink points to.
    Returns list of deleted (or would-be-deleted) paths.
    """
    re = resolved_endpoints or {}
    snapshots = list_snapshots(sync, config, re)
    excess = len(snapshots) - max_snapshots
    if excess <= 0:
        return []
    else:
        latest = read_latest_symlink(sync, config, resolved_endpoints=re)
        base = snapshots_dir(sync, config)

        # Candidates: oldest first, skip the latest target, take up to excess
        to_delete = [
            f"{base}/{s.name}"
            for s in snapshots
            if latest is None or s.name != latest.name
        ][:excess]

        if not dry_run:
            dst_vol = destination_volume(sync, config)
            for path in to_delete:
                delete_snapshot(path, dst_vol, re)

        return to_delete
