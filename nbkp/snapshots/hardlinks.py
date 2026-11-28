"""Hard-link snapshots: plain directories that rsync fills using ``--link-dest``."""

import shutil
import stat
from datetime import datetime
from pathlib import Path

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
    run_snapshot_command,
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
    resolved = resolved_endpoints or {}
    dst_vol = destination_volume(sync, config)
    snapshot_path = snapshot_path_for(sync, config, now=now, platform=platform)
    parent = snapshots_dir(sync, config)
    run_checked(
        SnapshotOp.MKDIR,
        parent,
        ["mkdir", "-p", parent],
        dst_vol,
        resolved,
        run=run,
    )
    run_checked(
        SnapshotOp.MKDIR,
        snapshot_path,
        ["mkdir", snapshot_path],
        dst_vol,
        resolved,
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
    resolved = resolved_endpoints or {}
    latest = _last_complete_snapshot(sync, config, resolved, run)
    base = snapshots_dir(sync, config)
    orphans = [
        f"{base}/{s.name}"
        for s in list_snapshots(sync, config, resolved, run=run)
        if latest is None or s.timestamp > latest.timestamp
    ]
    dst_vol = destination_volume(sync, config)
    for path in orphans:
        delete_snapshot(path, dst_vol, resolved, run=run)
    return orphans


def _last_complete_snapshot(
    sync: SyncConfig,
    config: Config,
    resolved: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> Snapshot | None:
    """
    Return the snapshot ``latest`` designates, ``None`` while none completed.

    Raises :class:`SnapshotOperationError` when ``latest`` is absent.
    """
    match read_latest_link(sync, config, resolved_endpoints=resolved, run=run):
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

    ``rsync -a`` preserves directory modes, so a snapshot of a read-only tree (a Go
    module cache, a Nix store) holds directories its owner cannot delete from. When
    the plain deletion fails, the snapshot's directories are given their owner's
    read, write and search bits and the deletion is retried. Only directories are
    changed: files are hard-linked to other snapshots, and a mode change on one would
    show in every snapshot sharing it.

    Raises :class:`SnapshotOperationError` when the deletion fails.
    """
    match volume:
        case RemoteVolume():
            first = run_snapshot_command(
                SnapshotOp.DELETE,
                path,
                build_rm_tree_args(path),
                volume,
                resolved_endpoints,
                run=run,
            )
            if first.returncode != 0:
                for args in (build_owner_rwx_dirs_args(path), build_rm_tree_args(path)):
                    run_checked(
                        SnapshotOp.DELETE,
                        path,
                        args,
                        volume,
                        resolved_endpoints,
                        run=run,
                    )
        case LocalVolume():
            local_fs_op(SnapshotOp.DELETE, path, lambda: _rmtree_local(path))


def build_rm_tree_args(path: str) -> list[str]:
    """Return the command deleting the directory tree at *path*."""
    return ["rm", "-rf", path]


def build_owner_rwx_dirs_args(path: str) -> list[str]:
    """
    Return the command giving the owner ``rwx`` on every directory of *path*.

    ``-exec ... ;`` runs ``chmod`` on each directory as ``find`` reaches it, before
    ``find`` lists it, so a directory nobody could list is opened before it is
    walked. ``find`` does not follow symlinks, and ``-type d`` leaves files, which
    other snapshots share through hard links, untouched.
    """
    return [
        "find",
        path,
        "-type",
        "d",
        "!",
        "-perm",
        "-0700",
        "-exec",
        "chmod",
        "u+rwx",
        "{}",
        ";",
    ]


def _rmtree_local(path: str) -> None:
    try:
        shutil.rmtree(path)
    except PermissionError:
        _give_owner_rwx_to_dirs(path)
        shutil.rmtree(path)


def _give_owner_rwx_to_dirs(top: str) -> None:
    """
    Add the owner's ``rwx`` bits to *top* and every directory below it.

    Top-down, each directory before its children are listed, so that a directory
    its owner could not even list is opened up before it is walked. Symlinks are
    left alone: ``chmod`` would follow them out of the snapshot.
    """
    _add_owner_rwx(Path(top))
    for dirpath, dirnames, _ in Path(top).walk():
        for name in dirnames:
            _add_owner_rwx(dirpath / name)


def _add_owner_rwx(path: Path) -> None:
    mode = path.lstat().st_mode
    if stat.S_ISDIR(mode) and mode & stat.S_IRWXU != stat.S_IRWXU:
        path.chmod(stat.S_IMODE(mode) | stat.S_IRWXU)


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
