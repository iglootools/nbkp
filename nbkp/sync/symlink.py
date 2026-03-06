"""Latest-symlink management (shared by hard-link and btrfs)."""

from __future__ import annotations

from pathlib import Path

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
)
from ..remote import run_remote_command
from .btrfs import LATEST_LINK, SNAPSHOTS_DIR, resolve_dest_path


def read_latest_symlink(
    sync: SyncConfig,
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str | None:
    """Read the latest symlink target, returning the snapshot name.

    Returns None if the symlink does not exist.
    """
    re = resolved_endpoints or {}
    dest_path = resolve_dest_path(sync, config)
    latest_path = f"{dest_path}/{LATEST_LINK}"

    dst_vol = config.volumes[sync.destination.volume]
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

    dst_vol = config.volumes[sync.destination.volume]
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
