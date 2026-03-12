"""Btrfs snapshot creation, lookup, and pruning."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from ...conventions import SNAPSHOTS_DIR, STAGING_DIR
from ...remote import run_remote_command
from .common import (
    format_snapshot_timestamp,
    list_snapshots,
    resolve_dest_path,
)


def _run_on_volume(
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> subprocess.CompletedProcess[str]:
    """Run a command on the volume's host (local or remote)."""
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            return run_remote_command(ep.server, cmd, ep.proxy_chain)
        case LocalVolume():
            return subprocess.run(cmd, capture_output=True, text=True)


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
    if now is None:
        now = datetime.now(timezone.utc)
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    dest_path = resolve_dest_path(sync, config)
    timestamp = format_snapshot_timestamp(now, dst_vol)
    snapshot_path = f"{dest_path}/{SNAPSHOTS_DIR}/{timestamp}"
    tmp_path = f"{dest_path}/{STAGING_DIR}"
    result = _run_on_volume(
        ["btrfs", "subvolume", "snapshot", "-r", tmp_path, snapshot_path],
        dst_vol,
        re,
    )
    if result.returncode != 0:
        raise RuntimeError(f"btrfs snapshot failed: {result.stderr}")
    return snapshot_path


def _make_snapshot_writable(
    path: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Unset the readonly property so the snapshot can be deleted."""
    result = _run_on_volume(
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
    result = _run_on_volume(
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

    latest_name = read_latest_symlink(sync, config, resolved_endpoints=re)

    # Candidates: oldest first, skip the latest target, take up to excess
    to_delete = [p for p in snapshots if p.rsplit("/", 1)[-1] != latest_name][:excess]

    if not dry_run:
        dst = config.destination_endpoint(sync)
        dst_vol = config.volumes[dst.volume]
        for path in to_delete:
            delete_snapshot(path, dst_vol, re)

    return to_delete
