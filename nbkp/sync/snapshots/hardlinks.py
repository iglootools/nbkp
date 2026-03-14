"""Hard-link snapshot creation, lookup, symlink management, and pruning."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from ...fsprotocol import SNAPSHOTS_DIR
from ...remote.dispatch import run_on_volume
from .common import (
    create_snapshot_timestamp,
    list_snapshots,
    read_latest_symlink,
    resolve_dest_path,
)


def create_snapshot_dir(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """Create a snapshot directory for the current sync.

    Returns the full snapshot path.
    """
    re = resolved_endpoints or {}
    ts = now or datetime.now(timezone.utc)
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    dest_path = resolve_dest_path(sync, config)
    snapshot = create_snapshot_timestamp(ts, dst_vol)
    snapshot_path = f"{dest_path}/{SNAPSHOTS_DIR}/{snapshot.name}"
    result = run_on_volume(["mkdir", "-p", snapshot_path], dst_vol, re)

    if result.returncode != 0:
        raise RuntimeError(f"mkdir snapshot dir failed: {result.stderr}")
    else:
        return snapshot_path


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
        all_snapshots = list_snapshots(sync, config, re)
        dst = config.destination_endpoint(sync)
        dst_vol = config.volumes[dst.volume]
        dest_path = resolve_dest_path(sync, config)
        snapshots_dir = f"{dest_path}/{SNAPSHOTS_DIR}"
        orphans = [
            f"{snapshots_dir}/{s.name}"
            for s in all_snapshots
            if s.timestamp > latest.timestamp
        ]
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
            result = run_on_volume(["rm", "-rf", path], volume, resolved_endpoints)
            if result.returncode != 0:
                raise RuntimeError(f"rm -rf snapshot failed: {result.stderr}")
        case LocalVolume():
            shutil.rmtree(path)


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
        dest_path = resolve_dest_path(sync, config)
        snapshots_dir = f"{dest_path}/{SNAPSHOTS_DIR}"

        # Candidates: oldest first, skip the latest target, take up to excess
        to_delete = [
            f"{snapshots_dir}/{s.name}"
            for s in snapshots
            if latest is None or s.name != latest.name
        ][:excess]

        if not dry_run:
            dst = config.destination_endpoint(sync)
            dst_vol = config.volumes[dst.volume]
            for path in to_delete:
                delete_snapshot(path, dst_vol, re)

        return to_delete
