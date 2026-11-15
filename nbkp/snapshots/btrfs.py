from datetime import datetime

from ..config import (
    Config,
    SyncConfig,
    Volume,
)
from ..fsprotocol import STAGING_DIR
from ..remote.endpoints import ResolvedEndpoints
from .common import (
    create_snapshot_timestamp,
    destination_volume,
    prune_oldest_snapshots,
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
    """See :func:`.common.prune_oldest_snapshots`."""
    return prune_oldest_snapshots(
        sync,
        config,
        max_snapshots,
        delete_snapshot,
        dry_run=dry_run,
        resolved_endpoints=resolved_endpoints,
    )
