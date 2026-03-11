"""Snapshot constants, shared helpers, and latest-symlink management.

Items shared by both hard-link and btrfs snapshot backends.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
)
from ...remote import run_remote_command

#: Directory name that holds timestamped snapshots (both btrfs
#: and hard-link).
SNAPSHOTS_DIR = "snapshots"

#: Symlink name that points to the most recent complete snapshot.
LATEST_LINK = "latest"

DEVNULL_TARGET = "/dev/null"


def resolve_dest_path(sync: SyncConfig, config: Config) -> str:
    """Resolve the destination path for a sync."""
    dst = config.destination_endpoint(sync)
    vol = config.volumes[dst.volume]
    if dst.subdir:
        return f"{vol.path}/{dst.subdir}"
    else:
        return vol.path


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
    match dst_vol:
        case RemoteVolume():
            ep = re[dst_vol.slug]
            result = run_remote_command(
                ep.server,
                ["ls", snapshots_dir],
                ep.proxy_chain,
            )
        case LocalVolume():
            result = subprocess.run(
                ["ls", snapshots_dir],
                capture_output=True,
                text=True,
            )

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
    match dst_vol:
        case LocalVolume():
            p = Path(latest_path)
            if not p.is_symlink():
                return None
            target = str(p.readlink())
        case RemoteVolume():
            ep = re[dst_vol.slug]
            result = run_remote_command(
                ep.server,
                ["readlink", latest_path],
                ep.proxy_chain,
            )
            if result.returncode != 0:
                return None
            target = result.stdout.strip()

    if target == DEVNULL_TARGET:
        return None

    # Target is like "snapshots/{name}" — extract the name
    if "/" in target:
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
            ep = re[dst_vol.slug]
            result = run_remote_command(
                ep.server,
                ["ln", "-sfn", target, latest_path],
                ep.proxy_chain,
            )
            if result.returncode != 0:
                raise RuntimeError(f"symlink update failed: {result.stderr}")
