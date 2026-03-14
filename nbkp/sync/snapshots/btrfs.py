"""Btrfs snapshot creation, lookup, and pruning."""

from __future__ import annotations

from datetime import datetime, timezone

from ...config import (
    Config,
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from ...fsprotocol import SNAPSHOTS_DIR, STAGING_DIR
from ...remote.dispatch import run_on_volume
from .common import (
    create_snapshot_timestamp,
    list_snapshots,
    resolve_dest_path,
)


def create_snapshot(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """Create a read-only btrfs snapshot of staging/ into snapshots/.

    Returns the snapshot path.
    """
    re = resolved_endpoints or {}
    ts = now or datetime.now(timezone.utc)
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    dest_path = resolve_dest_path(sync, config)
    snapshot = create_snapshot_timestamp(ts, dst_vol)
    snapshot_path = f"{dest_path}/{SNAPSHOTS_DIR}/{snapshot.name}"
    tmp_path = f"{dest_path}/{STAGING_DIR}"
    result = run_on_volume(
        ["btrfs", "subvolume", "snapshot", "-r", tmp_path, snapshot_path],
        dst_vol,
        re,
    )
    if result.returncode != 0:
        raise RuntimeError(f"btrfs snapshot failed: {result.stderr}")
    else:
        return snapshot_path


def _make_snapshot_writable(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Unset the readonly property so the snapshot can be deleted."""
    result = run_on_volume(
        ["btrfs", "property", "set", path, "ro", "false"],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        raise RuntimeError(f"btrfs property set ro=false failed: {result.stderr}")


def delete_snapshot(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Delete a single btrfs snapshot subvolume.

    First unsets the readonly property (needed when the filesystem
    is mounted with user_subvol_rm_allowed instead of granting
    CAP_SYS_ADMIN), then deletes the subvolume.
    """
    _make_snapshot_writable(path, volume, resolved_endpoints)
    result = run_on_volume(
        ["btrfs", "subvolume", "delete", path],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        raise RuntimeError(f"btrfs delete failed: {result.stderr}")


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
    from .common import read_latest_symlink

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
