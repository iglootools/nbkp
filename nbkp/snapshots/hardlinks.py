"""Hard-link snapshots: plain directories that rsync fills using ``--link-dest``."""

import shutil
from datetime import datetime

from nbkp.config import Config, LocalVolume, RemoteVolume, SyncConfig, Volume
from nbkp.fsprotocol import LATEST_LINK, Snapshot
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.common import (
    NoLatestSnapshot,
    create_snapshot_timestamp,
    destination_volume,
    list_snapshots,
    prune_oldest_snapshots,
    read_latest_link,
    resolve_dest_path,
    snapshots_dir,
)
from nbkp.snapshots.errors import (
    SnapshotOp,
    SnapshotOperationError,
    local_fs_op,
    run_checked,
)


def create_snapshot_dir(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> str:
    """
    Create the snapshot directory, returning its full path.

    *now* names the snapshot and *platform* (``sys.platform``) decides the macOS-safe
    name form; both come from the caller.

    The snapshot directory must not exist yet: reusing one (two runs that
    get the same name) would have rsync write into a complete snapshot,
    possibly the one ``latest`` points to, and a failed rsync would then
    delete it.  Only ``snapshots/`` itself is created when missing.
    """
    re = resolved_endpoints or {}
    dst_vol = destination_volume(sync, config)
    snapshot_path = snapshot_path_for(sync, config, now=now, platform=platform)
    parent = snapshots_dir(sync, config)
    run_checked(SnapshotOp.MKDIR, parent, ["mkdir", "-p", parent], dst_vol, re, run=run)
    run_checked(
        SnapshotOp.MKDIR,
        snapshot_path,
        ["mkdir", snapshot_path],
        dst_vol,
        re,
        run=run,
    )
    return snapshot_path


def snapshot_path_for(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
) -> str:
    """
    Return the path :func:`create_snapshot_dir` would create, without creating it.

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
    run: VolumeCommandRunner = run_on_volume,
) -> list[str]:
    """
    Remove the snapshots newer than the one ``latest`` designates.

    They are the leftovers of failed syncs. Returns the deleted paths.

    When ``latest`` points to ``/dev/null``, no sync has completed yet, so
    every snapshot directory is a leftover, including one a first run left
    half-written.  Kept, it would sort before the next complete snapshot and
    pass for a complete one itself.

    When ``latest`` is absent, nothing is deleted and
    :class:`SnapshotOperationError` is raised: nbkp never removes the link
    (init points it at ``/dev/null``), so its absence is outside damage, and
    the snapshots may well be complete.
    """
    re = resolved_endpoints or {}
    latest = _last_complete_snapshot(sync, config, re, run)
    base = snapshots_dir(sync, config)
    orphans = [
        f"{base}/{s.name}"
        for s in list_snapshots(sync, config, re, run=run)
        if latest is None or s.timestamp > latest.timestamp
    ]
    dst_vol = destination_volume(sync, config)
    for path in orphans:
        delete_snapshot(path, dst_vol, re, run=run)
    return orphans


def _last_complete_snapshot(
    sync: SyncConfig,
    config: Config,
    re: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> Snapshot | None:
    """
    Return the snapshot ``latest`` designates, ``None`` while none completed.

    Raises :class:`SnapshotOperationError` when ``latest`` is absent.
    """
    match read_latest_link(sync, config, resolved_endpoints=re, run=run):
        case Snapshot() as latest:
            return latest
        case NoLatestSnapshot.NONE_YET:
            return None
        case NoLatestSnapshot.ABSENT:
            latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
            raise SnapshotOperationError(
                SnapshotOp.READ_LATEST,
                latest_path,
                "latest symlink not found; orphaned snapshots left in place",
            )


def delete_snapshot(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> None:
    """
    Delete the hard-link snapshot directory at *path* with its contents.

    Raises :class:`SnapshotOperationError` when the deletion fails.
    """
    match volume:
        case RemoteVolume():
            run_checked(
                SnapshotOp.DELETE,
                path,
                ["rm", "-rf", path],
                volume,
                resolved_endpoints,
                run=run,
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
