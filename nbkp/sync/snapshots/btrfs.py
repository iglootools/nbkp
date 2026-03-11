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
from ...remote import run_remote_command
from .common import SNAPSHOTS_DIR, list_snapshots, resolve_dest_path

#: Directory name for the writable btrfs subvolume that rsync
#: syncs into before a read-only snapshot is created.
STAGING_DIR = "staging"


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
    dest_path = resolve_dest_path(sync, config)
    # isoformat uses +00:00, but Z is more conventional for UTC.
    timestamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    snapshot_path = f"{dest_path}/{SNAPSHOTS_DIR}/{timestamp}"
    tmp_path = f"{dest_path}/{STAGING_DIR}"

    cmd = [
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        tmp_path,
        snapshot_path,
    ]

    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    match dst_vol:
        case RemoteVolume():
            ep = re[dst_vol.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
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
    cmd = ["btrfs", "property", "set", path, "ro", "false"]
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
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

    cmd = ["btrfs", "subvolume", "delete", path]
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
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

    # Candidates are oldest first, but skip the latest target
    to_delete: list[str] = []
    for snap_path in snapshots:
        if len(to_delete) >= excess:
            break
        snap_name = snap_path.rsplit("/", 1)[-1]
        if snap_name == latest_name:
            continue
        to_delete.append(snap_path)

    if not dry_run:
        dst = config.destination_endpoint(sync)
        dst_vol = config.volumes[dst.volume]
        for path in to_delete:
            delete_snapshot(path, dst_vol, re)

    return to_delete
