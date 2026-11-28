"""Btrfs snapshots: read-only subvolume snapshots of the ``staging/`` subvolume."""

from datetime import datetime

from nbkp.config import Config, SyncConfig, Volume
from nbkp.fsprotocol import STAGING_DIR
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.common import (
    create_snapshot_timestamp,
    destination_volume,
    prune_oldest_snapshots,
    resolve_dest_path,
    snapshots_dir,
)
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError, run_checked


def create_snapshot(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner = run_on_volume,
) -> str:
    """
    Create a read-only btrfs snapshot of staging/ into snapshots/.

    *now* names the snapshot and *platform* (``sys.platform``) decides the
    macOS-safe name form; both come from the caller so this stays
    deterministic.  Returns the snapshot path.
    """
    resolved = resolved_endpoints
    dst_vol = destination_volume(sync, config)
    snapshot = create_snapshot_timestamp(now, dst_vol, platform)
    snapshot_path = f"{snapshots_dir(sync, config)}/{snapshot.name}"
    staging_path = f"{resolve_dest_path(sync, config)}/{STAGING_DIR}"
    run_checked(
        SnapshotOp.CREATE,
        snapshot_path,
        build_create_snapshot_args(staging_path, snapshot_path),
        dst_vol,
        resolved,
        run=run,
    )
    return snapshot_path


def build_create_snapshot_args(staging_path: str, snapshot_path: str) -> list[str]:
    """Return the command snapshotting *staging_path* read-only at *snapshot_path*."""
    return ["btrfs", "subvolume", "snapshot", "-r", staging_path, snapshot_path]


def _readonly_cmd(path: str, *, readonly: bool) -> list[str]:
    return ["btrfs", "property", "set", path, "ro", "true" if readonly else "false"]


def _restore_readonly_failure(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> str | None:
    """Make *path* read-only again; why that failed, or ``None``."""
    try:
        run_checked(
            SnapshotOp.MAKE_READONLY,
            path,
            _readonly_cmd(path, readonly=True),
            volume,
            resolved_endpoints,
            run=run,
        )
    except SnapshotOperationError as e:
        return e.stderr.strip()
    else:
        return None


def delete_snapshot(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> None:
    """
    Delete a single btrfs snapshot subvolume.

    First unsets the readonly property (needed when the filesystem
    is mounted with user_subvol_rm_allowed instead of granting
    CAP_SYS_ADMIN), then deletes the subvolume.  When the delete fails,
    the snapshot is made read-only again, so one that stays is not left
    writable; the error says whether that worked.
    """
    run_checked(
        SnapshotOp.MAKE_WRITABLE,
        path,
        _readonly_cmd(path, readonly=False),
        volume,
        resolved_endpoints,
        run=run,
    )
    try:
        run_checked(
            SnapshotOp.DELETE,
            path,
            ["btrfs", "subvolume", "delete", path],
            volume,
            resolved_endpoints,
            run=run,
        )
    except SnapshotOperationError as e:
        restore_failure = _restore_readonly_failure(
            path,
            volume,
            resolved_endpoints,
            run,
        )
        note = (
            "the snapshot was made read-only again"
            if restore_failure is None
            else "making the snapshot read-only again also failed, so it is left"
            f" writable: {restore_failure}"
        )
        raise SnapshotOperationError(
            SnapshotOp.DELETE,
            path,
            f"{e.stderr.strip()}; {note}",
        ) from e


def prune_snapshots(
    sync: SyncConfig,
    config: Config,
    max_snapshots: int,
    *,
    dry_run: bool = False,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner = run_on_volume,
) -> list[str]:
    """See :func:`.common.prune_oldest_snapshots`."""
    return prune_oldest_snapshots(
        sync,
        config,
        max_snapshots,
        delete_snapshot,
        dry_run=dry_run,
        resolved_endpoints=resolved_endpoints,
        run=run,
    )
