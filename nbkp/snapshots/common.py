from datetime import datetime
from pathlib import Path
from typing import Protocol

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    Volume,
    resolve_endpoint_path,
)
from ..fsprotocol import DEVNULL_TARGET, LATEST_LINK, SNAPSHOTS_DIR, Snapshot
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoints
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
    macos_local = isinstance(volume, LocalVolume) and platform == "darwin"
    return Snapshot.create(now, macos_local=macos_local)


def resolve_dest_path(sync: SyncConfig, config: Config) -> str:
    dst = config.destination_endpoint(sync)
    return resolve_endpoint_path(config.volumes[dst.volume], dst.subdir)


def destination_volume(sync: SyncConfig, config: Config) -> Volume:
    return config.volumes[config.destination_endpoint(sync).volume]


def snapshots_dir(sync: SyncConfig, config: Config) -> str:
    return f"{resolve_dest_path(sync, config)}/{SNAPSHOTS_DIR}"


def _parse_snapshot_name(name: str) -> Snapshot | None:
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
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> bool:
    """Whether *path* is a directory; raises when that cannot be determined."""
    result = run_snapshot_command(
        SnapshotOp.LIST, path, ["test", "-d", path], volume, resolved_endpoints, run=run
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
    *,
    run: VolumeCommandRunner = run_on_volume,
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
    result = run_snapshot_command(
        SnapshotOp.LIST, path, ["ls", path], dst_vol, re, run=run
    )
    if result.returncode == 0:
        return parse_snapshot_listing(result.stdout)
    elif not _snapshots_dir_exists(path, dst_vol, re, run):
        return []
    else:
        raise SnapshotOperationError(SnapshotOp.LIST, path, result.stderr or "")


def get_latest_snapshot(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> Snapshot | None:
    snapshots = list_snapshots(sync, config, resolved_endpoints, run=run)
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
    run: VolumeCommandRunner,
) -> str | None:
    """``readlink``; on failure, ``test -L`` tells "absent" from "broken"."""
    op = SnapshotOp.READ_LATEST
    result = run_snapshot_command(
        op, latest_path, ["readlink", latest_path], volume, resolved_endpoints, run=run
    )
    if result.returncode == 0:
        return result.stdout.strip()
    else:
        probe = run_snapshot_command(
            op,
            latest_path,
            ["test", "-L", latest_path],
            volume,
            resolved_endpoints,
            run=run,
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
    run: VolumeCommandRunner,
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
            return _read_remote_symlink_target(
                volume, latest_path, resolved_endpoints, run
            )


def read_latest_symlink(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> Snapshot | None:
    """Read the latest symlink target, returning a Snapshot.

    Returns ``None`` if the symlink does not exist or points to
    ``/dev/null`` (the canonical "no snapshot yet" marker).  A target that
    is not a snapshot name raises
    :class:`~nbkp.snapshots.errors.SnapshotOperationError`: callers use it
    to protect the latest snapshot, so it must not be guessed at.
    """
    re = resolved_endpoints or {}
    latest_path = f"{resolve_dest_path(sync, config)}/{LATEST_LINK}"
    target = _read_raw_symlink_target(
        destination_volume(sync, config), latest_path, re, run
    )

    if target is None or target == DEVNULL_TARGET:
        return None
    else:
        name = target.rsplit("/", 1)[-1] if "/" in target else target
        try:
            return Snapshot.from_name(name)
        except ValueError as e:
            raise SnapshotOperationError(
                SnapshotOp.READ_LATEST, latest_path, f"not a snapshot: {target}"
            ) from e


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
    run: VolumeCommandRunner = run_on_volume,
) -> None:
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
    ) -> None: ...


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
    """Delete oldest snapshots exceeding max_snapshots with the backend's
    *delete_snapshot*.

    Never prunes the snapshot that the latest symlink points to.
    Returns list of deleted (or would-be-deleted) paths.
    """
    re = resolved_endpoints or {}
    snapshots = list_snapshots(sync, config, re, run=run)
    excess = len(snapshots) - max_snapshots
    if excess <= 0:
        return []
    else:
        latest = read_latest_symlink(sync, config, resolved_endpoints=re, run=run)
        base = snapshots_dir(sync, config)

        # list_snapshots sorts oldest first, so the slice takes the oldest.
        to_delete = [
            f"{base}/{s.name}"
            for s in snapshots
            if latest is None or s.name != latest.name
        ][:excess]

        if not dry_run:
            dst_vol = destination_volume(sync, config)
            for path in to_delete:
                delete_snapshot(path, dst_vol, re, run=run)

        return to_delete
