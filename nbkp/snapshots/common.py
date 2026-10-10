"""Snapshot shared helpers and latest-symlink management.

Items shared by both hard-link and btrfs snapshot backends.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..fsprotocol import DEVNULL_TARGET, LATEST_LINK, SNAPSHOTS_DIR, Snapshot
from .errors import (
    SnapshotOp,
    SnapshotOperationError,
    local_fs_op,
    run_checked,
    run_snapshot_command,
)


def create_snapshot_timestamp(
    now: datetime,
    volume: Volume,
    platform: str,
) -> Snapshot:
    """Create a Snapshot for the given timestamp and volume.

    Resolves the macOS local-volume flag from the volume type and the
    orchestrator *platform* (``sys.platform``, supplied by the caller) and
    delegates to ``Snapshot.create``.
    """
    macos_local = isinstance(volume, LocalVolume) and platform == "darwin"
    return Snapshot.create(now, macos_local=macos_local)


def resolve_dest_path(sync: SyncConfig, config: Config) -> str:
    """Resolve the destination path for a sync."""
    dst = config.destination_endpoint(sync)
    vol = config.volumes[dst.volume]
    if vol.path is None:
        msg = f"volume '{vol.slug}': mount path not resolved"
        raise ValueError(msg)
    if dst.subdir:
        return f"{vol.path}/{dst.subdir}"
    else:
        return vol.path


def destination_volume(sync: SyncConfig, config: Config) -> Volume:
    """The volume holding a sync's destination endpoint."""
    return config.volumes[config.destination_endpoint(sync).volume]


def snapshots_dir(sync: SyncConfig, config: Config) -> str:
    """The ``snapshots/`` directory of a sync's destination."""
    return f"{resolve_dest_path(sync, config)}/{SNAPSHOTS_DIR}"


def _parse_snapshot_name(name: str) -> Snapshot | None:
    """Parse a ``snapshots/`` entry, or ``None`` for a non-snapshot entry."""
    try:
        return Snapshot.from_name(name)
    except ValueError:
        return None


def parse_snapshot_listing(stdout: str) -> list[Snapshot]:
    """Parse ``ls`` output into snapshots sorted oldest-first.

    Entries that are not snapshot timestamps (``lost+found`` at the root
    of a dedicated filesystem, ``.DS_Store``, a stray file an operator
    left behind) are ignored rather than treated as errors: nbkp never
    created them, so it neither counts nor prunes them.  The generated
    shell script filters the same way (``nbkp_snapshot_names``).
    """
    return [
        snapshot
        for name in sorted(line for line in stdout.splitlines() if line.strip())
        for snapshot in [_parse_snapshot_name(name.strip())]
        if snapshot is not None
    ]


def _snapshots_dir_exists(
    path: str, volume: Volume, resolved_endpoints: ResolvedEndpoints
) -> bool:
    """Whether *path* is a directory; raises when that cannot be determined."""
    result = run_snapshot_command(
        SnapshotOp.LIST, path, ["test", "-d", path], volume, resolved_endpoints
    )
    match result.returncode:
        case 0:
            return True
        case 1:
            return False
        case _:
            raise SnapshotOperationError(SnapshotOp.LIST, path, result.stderr or "")


def list_snapshots(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[Snapshot]:
    """List all snapshots sorted oldest-first.

    An absent ``snapshots/`` directory means no snapshots yet and yields
    ``[]``; any other listing failure raises
    :class:`~nbkp.snapshots.errors.SnapshotOperationError`, so pruning
    never acts on a listing it could not read.
    """
    re = resolved_endpoints or {}
    path = snapshots_dir(sync, config)
    dst_vol = destination_volume(sync, config)
    result = run_snapshot_command(SnapshotOp.LIST, path, ["ls", path], dst_vol, re)
    if result.returncode == 0:
        return parse_snapshot_listing(result.stdout)
    elif not _snapshots_dir_exists(path, dst_vol, re):
        return []
    else:
        raise SnapshotOperationError(SnapshotOp.LIST, path, result.stderr or "")


def get_latest_snapshot(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> Snapshot | None:
    """Get the most recent snapshot, or None."""
    snapshots = list_snapshots(sync, config, resolved_endpoints)
    if snapshots:
        return snapshots[-1]
    else:
        return None


def _read_local_symlink_target(latest_path: str) -> str | None:
    p = Path(latest_path)
    if not local_fs_op(SnapshotOp.READ_LATEST, latest_path, p.is_symlink):
        return None
    else:
        return str(local_fs_op(SnapshotOp.READ_LATEST, latest_path, p.readlink))


def _read_remote_symlink_target(
    volume: Volume,
    latest_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """``readlink``; on failure, ``test -L`` tells "absent" from "broken"."""
    op = SnapshotOp.READ_LATEST
    result = run_snapshot_command(
        op, latest_path, ["readlink", latest_path], volume, resolved_endpoints
    )
    if result.returncode == 0:
        return result.stdout.strip()
    else:
        probe = run_snapshot_command(
            op, latest_path, ["test", "-L", latest_path], volume, resolved_endpoints
        )
        match probe.returncode:
            case 1:
                return None
            case _:
                raise SnapshotOperationError(op, latest_path, result.stderr or "")


def _read_raw_symlink_target(
    volume: Volume,
    latest_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Read the raw symlink target string, or None if there is no symlink.

    A symlink that exists but cannot be read raises instead of returning
    ``None``: callers use the target to protect the latest snapshot from
    pruning, so "unknown" must never be mistaken for "absent".
    """
    match volume:
        case LocalVolume():
            return _read_local_symlink_target(latest_path)
        case RemoteVolume():
            return _read_remote_symlink_target(volume, latest_path, resolved_endpoints)


def read_latest_symlink(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> Snapshot | None:
    """Read the latest symlink target, returning a Snapshot.

    Returns ``None`` if the symlink does not exist or points to
    ``/dev/null`` (the canonical "no snapshot yet" marker).
    """
    re = resolved_endpoints or {}
    latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
    target = _read_raw_symlink_target(destination_volume(sync, config), latest_path, re)

    if target is None or target == DEVNULL_TARGET:
        return None
    else:
        name = target.rsplit("/", 1)[-1] if "/" in target else target
        return Snapshot.from_name(name)


def _replace_local_symlink(latest_path: str, target: str) -> None:
    p = Path(latest_path)
    p.unlink(missing_ok=True)
    p.symlink_to(target)


def update_latest_symlink(
    sync: SyncConfig,
    config: Config,
    snapshot: Snapshot,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> None:
    """Create or update the latest symlink to point to a snapshot."""
    re = resolved_endpoints or {}
    latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
    target = f"{SNAPSHOTS_DIR}/{snapshot.name}"
    op = SnapshotOp.UPDATE_LATEST

    match destination_volume(sync, config):
        case LocalVolume():
            local_fs_op(
                op, latest_path, lambda: _replace_local_symlink(latest_path, target)
            )
        case RemoteVolume() as dst_vol:
            run_checked(
                op, latest_path, ["ln", "-sfn", target, latest_path], dst_vol, re
            )
