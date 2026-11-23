"""Snapshot operations shared by the btrfs and hard-link backends."""

import enum
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    Volume,
    resolve_endpoint_path,
)
from nbkp.fsprotocol import (
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    Snapshot,
)
from nbkp.preflight import VolumeReadError, list_snapshot_dir, read_symlink
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import ssh_connection_errors
from nbkp.snapshots.errors import (
    SnapshotOp,
    SnapshotOperationError,
    SnapshotPruneError,
    local_fs_op,
    run_checked,
)


def create_snapshot_timestamp(
    now: datetime,
    volume: Volume,
    platform: str,
) -> Snapshot:
    """
    Return the snapshot to create at *now* on *volume*.

    On a local volume under macOS (*platform* is ``sys.platform``) the name
    uses hyphens instead of colons, which APFS/HFS+ forbid in filenames.
    """
    macos_local = isinstance(volume, LocalVolume) and platform == "darwin"
    return Snapshot.create(now, macos_local=macos_local)


def resolve_dest_path(sync: SyncConfig, config: Config) -> str:
    """Return the absolute path of *sync*'s destination endpoint on its volume."""
    dst = config.destination_endpoint(sync)
    return resolve_endpoint_path(config.volumes[dst.volume], dst.subdir)


def destination_volume(sync: SyncConfig, config: Config) -> Volume:
    """Return the volume that holds *sync*'s destination."""
    return config.volumes[config.destination_endpoint(sync).volume]


def snapshots_dir(sync: SyncConfig, config: Config) -> str:
    """Return the path of the ``snapshots/`` directory under *sync*'s destination."""
    return f"{resolve_dest_path(sync, config)}/{SNAPSHOTS_DIR}"


def ensure_after_latest(
    sync: SyncConfig,
    config: Config,
    *,
    now: datetime,
    platform: str,
    latest: Snapshot | None,
    op: SnapshotOp,
) -> None:
    """
    Refuse to name a new snapshot that would not sort after *latest*.

    Snapshot names are timestamps, and orphan cleanup treats every
    snapshot newer than ``latest`` as a leftover of a failed sync.  A
    snapshot taken while the clock is behind (a host without NTP yet, a
    manual change) would move ``latest`` back in time, and the next run
    would delete every complete snapshot taken before as an orphan.  A
    name equal to ``latest``'s would reuse the latest complete snapshot.
    Either way the sync must fail instead.
    """
    snapshot = create_snapshot_timestamp(
        now,
        destination_volume(sync, config),
        platform,
    )
    if latest is not None and snapshot.timestamp <= latest.timestamp:
        path = f"{snapshots_dir(sync, config)}/{snapshot.name}"
        msg = (
            f"new snapshot {snapshot.name} would not be after the latest "
            f"snapshot {latest.name}; is the clock behind?"
        )
        raise SnapshotOperationError(op, path, msg)


def list_snapshots(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> list[Snapshot]:
    """
    List all snapshots sorted oldest-first.

    An absent ``snapshots/`` directory means no snapshots yet and yields
    ``[]``; any other listing failure raises
    :class:`~nbkp.snapshots.errors.SnapshotOperationError`, so pruning
    never acts on a listing it could not read.
    """
    path = snapshots_dir(sync, config)
    try:
        return list_snapshot_dir(
            destination_volume(sync, config),
            path,
            resolved_endpoints or {},
            run=run,
        )
    except VolumeReadError as e:
        raise SnapshotOperationError(SnapshotOp.LIST, path, e.detail) from e
    except ssh_connection_errors() as e:
        raise SnapshotOperationError(SnapshotOp.LIST, path, str(e)) from e


def _read_raw_symlink_target(
    volume: Volume,
    latest_path: str,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> str | None:
    """
    Read the raw symlink target string, or None if there is no symlink.

    A symlink that exists but cannot be read raises instead of returning
    ``None``: callers use the target to protect the latest snapshot from
    pruning, so "unknown" must never be mistaken for "absent".
    """
    op = SnapshotOp.READ_LATEST
    try:
        return read_symlink(volume, latest_path, resolved_endpoints, run=run)
    except VolumeReadError as e:
        raise SnapshotOperationError(op, latest_path, e.detail) from e
    except ssh_connection_errors() as e:
        # Transport errors and local filesystem errors, both OSError.
        raise SnapshotOperationError(op, latest_path, str(e)) from e


class NoLatestSnapshot(enum.Enum):
    """Why ``latest`` designates no snapshot, as :func:`read_latest_link` reports it."""

    ABSENT = "absent"
    """There is no ``latest`` symlink: nbkp never removes it, so it was lost."""
    NONE_YET = "none-yet"
    """``latest`` points to ``/dev/null``: no sync has completed yet."""


def read_latest_link(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> Snapshot | NoLatestSnapshot:
    """
    Read the snapshot *sync*'s ``latest`` symlink designates.

    Tells an absent symlink from one pointing to ``/dev/null`` (the
    canonical "no snapshot yet" marker), for callers that act differently on
    each.  A target that is not ``snapshots/<name>`` (see
    :meth:`Snapshot.from_latest_target`) raises
    :class:`~nbkp.snapshots.errors.SnapshotOperationError`: callers use it to
    protect the latest snapshot, so it must not be guessed at.
    """
    re = resolved_endpoints or {}
    latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
    target = _read_raw_symlink_target(
        destination_volume(sync, config),
        latest_path,
        re,
        run,
    )

    match target:
        case None:
            return NoLatestSnapshot.ABSENT
        case str() if target == DEVNULL_TARGET:
            return NoLatestSnapshot.NONE_YET
        case _:
            try:
                return Snapshot.from_latest_target(target)
            except ValueError as e:
                raise SnapshotOperationError(
                    SnapshotOp.READ_LATEST,
                    latest_path,
                    f"not a snapshot: {target}",
                ) from e


def read_latest_symlink(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> Snapshot | None:
    """
    Read the latest symlink target, returning a Snapshot.

    Returns ``None`` if the symlink does not exist or points to
    ``/dev/null``; :func:`read_latest_link` tells the two apart.  An
    invalid target raises as there.
    """
    match read_latest_link(
        sync,
        config,
        resolved_endpoints=resolved_endpoints,
        run=run,
    ):
        case Snapshot() as snapshot:
            return snapshot
        case NoLatestSnapshot():
            return None


def _replace_local_symlink(latest_path: str, target: str) -> None:
    """
    Point *latest_path* at *target* in one atomic step.

    The link is made under a temporary name next to it and renamed over
    ``latest``, so an interruption leaves either the old or the new link,
    never none (which preflight would report as a missing ``latest``).
    """
    p = Path(latest_path)
    tmp = p.with_name(f".{p.name}.nbkp-tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    try:
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def update_latest_symlink(
    sync: SyncConfig,
    config: Config,
    snapshot: Snapshot,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> None:
    """
    Point *sync*'s ``latest`` symlink at *snapshot*.

    The target is relative (``snapshots/<name>``).  Locally the link is
    replaced atomically; on a remote volume ``ln -sfn`` replaces it.
    Raises :class:`SnapshotOperationError` when the update fails.
    """
    re = resolved_endpoints or {}
    latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
    target = f"{SNAPSHOTS_DIR}/{snapshot.name}"
    op = SnapshotOp.UPDATE_LATEST

    match destination_volume(sync, config):
        case LocalVolume():
            local_fs_op(
                op,
                latest_path,
                lambda: _replace_local_symlink(latest_path, target),
            )
        case RemoteVolume() as dst_vol:
            run_checked(
                op,
                latest_path,
                ["ln", "-sfn", target, latest_path],
                dst_vol,
                re,
                run=run,
            )


class DeleteSnapshotFn(Protocol):
    """A backend's snapshot deletion, run through *run*."""

    def __call__(
        self,
        path: str,
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        run: VolumeCommandRunner = ...,
    ) -> None:
        """
        Delete the snapshot at *path* on *volume*.

        Raises :class:`SnapshotOperationError` when the deletion fails.
        """
        ...


def prune_oldest_snapshots(
    sync: SyncConfig,
    config: Config,
    max_snapshots: int,
    delete_snapshot: DeleteSnapshotFn,
    *,
    dry_run: bool = False,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> list[str]:
    """
    Delete the oldest snapshots beyond max_snapshots.

    Each is deleted with the backend's *delete_snapshot*.

    Never prunes the snapshot that the latest symlink points to, and
    neither counts nor prunes the snapshots newer than it: those are left
    by failed syncs, possibly incomplete, and orphan cleanup removes them.
    Counted, each would cost a complete snapshot its place.
    Returns list of deleted (or would-be-deleted) paths.  A failed deletion
    raises :class:`~nbkp.snapshots.errors.SnapshotPruneError`, which names
    the snapshots deleted before it.
    """
    re = resolved_endpoints or {}
    snapshots = list_snapshots(sync, config, re, run=run)
    if len(snapshots) <= max_snapshots:
        return []
    else:
        latest = read_latest_symlink(sync, config, resolved_endpoints=re, run=run)
        counted = [
            s for s in snapshots if latest is None or s.timestamp <= latest.timestamp
        ]
        excess = max(len(counted) - max_snapshots, 0)
        base = snapshots_dir(sync, config)

        # list_snapshots sorts oldest first, so the slice takes the oldest.
        to_delete = [
            f"{base}/{s.name}"
            for s in counted
            if latest is None or s.name != latest.name
        ][:excess]

        if not dry_run:
            dst_vol = destination_volume(sync, config)
            _delete_in_order(
                to_delete,
                lambda path: delete_snapshot(path, dst_vol, re, run=run),
            )

        return to_delete


def _delete_in_order(paths: list[str], delete: Callable[[str], None]) -> None:
    """
    Delete *paths* in order.

    A failure raises :class:`SnapshotPruneError` with the paths already deleted.
    """
    for i, path in enumerate(paths):
        try:
            delete(path)
        except SnapshotOperationError as e:
            raise SnapshotPruneError(e, tuple(paths[:i])) from e
