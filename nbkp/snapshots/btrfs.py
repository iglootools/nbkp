"""Btrfs snapshot creation, lookup, and pruning."""

from __future__ import annotations

from datetime import datetime

from ..config import (
    Config,
    SyncConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..fsprotocol import STAGING_DIR
from .common import (
    create_snapshot_timestamp,
    destination_volume,
    list_snapshots,
    read_latest_symlink,
    resolve_dest_path,
    snapshots_dir,
)
from .errors import SnapshotOp, run_checked


def create_snapshot(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """Create a read-only btrfs snapshot of staging/ into snapshots/.

    *now* names the snapshot and *platform* (``sys.platform``) decides the
    macOS-safe name form; both come from the caller so this stays
    deterministic.  Returns the snapshot path.
    """
    re = resolved_endpoints or {}
    dst_vol = destination_volume(sync, config)
    snapshot = create_snapshot_timestamp(now, dst_vol, platform)
    snapshot_path = f"{snapshots_dir(sync, config)}/{snapshot.name}"
    staging_path = f"{resolve_dest_path(sync, config)}/{STAGING_DIR}"
    run_checked(
        SnapshotOp.CREATE,
        snapshot_path,
        ["btrfs", "subvolume", "snapshot", "-r", staging_path, snapshot_path],
        dst_vol,
        re,
    )
    return snapshot_path


def _make_snapshot_writable(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Unset the readonly property so the snapshot can be deleted."""
    run_checked(
        SnapshotOp.MAKE_WRITABLE,
        path,
        ["btrfs", "property", "set", path, "ro", "false"],
        volume,
        resolved_endpoints,
    )


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
    run_checked(
        SnapshotOp.DELETE,
        path,
        ["btrfs", "subvolume", "delete", path],
        volume,
        resolved_endpoints,
    )


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
