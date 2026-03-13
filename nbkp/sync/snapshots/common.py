"""Snapshot shared helpers and latest-symlink management.

Items shared by both hard-link and btrfs snapshot backends.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from ...conventions import DEVNULL_TARGET, LATEST_LINK, SNAPSHOTS_DIR
from ...remote import run_remote_command


def format_snapshot_timestamp(
    now: datetime,
    volume: Volume,
    platform: str = sys.platform,
) -> str:
    """Format a UTC timestamp for use as a snapshot directory name.

    Uses standard ISO 8601 with colons on filesystems that support them
    (Linux local, all remote). Uses hyphens instead of colons on macOS
    local volumes because APFS/HFS+ forbids colons in filenames.
    """
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    match volume:
        case LocalVolume() if platform == "darwin":
            return ts.replace(":", "-")
        case _:
            return ts


def resolve_dest_path(sync: SyncConfig, config: Config) -> str:
    """Resolve the destination path for a sync."""
    dst = config.destination_endpoint(sync)
    vol = config.volumes[dst.volume]
    if dst.subdir:
        return f"{vol.path}/{dst.subdir}"
    else:
        return vol.path


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


def list_snapshots(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[str]:
    """List all snapshot paths sorted oldest-first."""
    re = resolved_endpoints or {}
    dest_path = resolve_dest_path(sync, config)
    snapshots_dir = f"{dest_path}/{SNAPSHOTS_DIR}"
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    result = _run_on_volume(["ls", snapshots_dir], dst_vol, re)

    if result.returncode != 0 or not result.stdout.strip():
        return []
    else:
        entries = sorted(result.stdout.strip().split("\n"))
        return [f"{snapshots_dir}/{e}" for e in entries]


def get_latest_snapshot(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str | None:
    """Get the path to the most recent snapshot, or None."""
    snapshots = list_snapshots(sync, config, resolved_endpoints)
    if snapshots:
        return snapshots[-1]
    else:
        return None


def _read_raw_symlink_target(
    volume: Volume,
    latest_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Read the raw symlink target string, or None if not found."""
    match volume:
        case LocalVolume():
            p = Path(latest_path)
            if not p.is_symlink():
                return None
            else:
                return str(p.readlink())
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(
                ep.server,
                ["readlink", latest_path],
                ep.proxy_chain,
            )
            if result.returncode != 0:
                return None
            else:
                return result.stdout.strip()


def read_latest_symlink(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str | None:
    """Read the latest symlink target, returning the snapshot name.

    Returns ``None`` if the symlink does not exist or points to
    ``/dev/null`` (the canonical "no snapshot yet" marker).
    """
    re = resolved_endpoints or {}
    dest_path = resolve_dest_path(sync, config)
    latest_path = f"{dest_path}/{LATEST_LINK}"
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    target = _read_raw_symlink_target(dst_vol, latest_path, re)

    if target is None or target == DEVNULL_TARGET:
        return None
    elif "/" in target:
        return target.rsplit("/", 1)[-1]
    else:
        return target


def update_latest_symlink(
    sync: SyncConfig,
    config: Config,
    snapshot_name: str,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> None:
    """Create or update the latest symlink to point to a snapshot."""
    re = resolved_endpoints or {}
    dest_path = resolve_dest_path(sync, config)
    latest_path = f"{dest_path}/{LATEST_LINK}"
    target = f"{SNAPSHOTS_DIR}/{snapshot_name}"

    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    match dst_vol:
        case LocalVolume():
            p = Path(latest_path)
            p.unlink(missing_ok=True)
            p.symlink_to(target)
        case RemoteVolume():
            result = _run_on_volume(["ln", "-sfn", target, latest_path], dst_vol, re)
            if result.returncode != 0:
                raise RuntimeError(f"symlink update failed: {result.stderr}")
