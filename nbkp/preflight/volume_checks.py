"""Volume availability checks."""

from __future__ import annotations

from pathlib import Path

from ..config import (
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    Volume,
)
from ..conventions import VOLUME_SENTINEL
from ..remote import run_remote_command
from .queries import (
    _check_command_available,
    _check_rsync_version,
)
from .snapshot_checks import (
    _check_btrfs_filesystem,
    _check_btrfs_mount_option,
    _check_hardlink_support,
)
from .status import VolumeCapabilities, VolumeError, VolumeStatus


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeStatus:
    """Check volume reachability and compute capabilities for active volumes.

    Returns a fully-populated ``VolumeStatus``: capabilities are ``None``
    only when the volume is inactive (unreachable / excluded / missing sentinel).
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            errors = _check_local_reachability(volume)
        case RemoteVolume():
            errors = _check_remote_reachability(volume, re)

    if errors:
        return VolumeStatus(slug=volume.slug, config=volume, errors=errors)

    caps = check_volume_capabilities(volume, re)
    return VolumeStatus(slug=volume.slug, config=volume, errors=[], capabilities=caps)


def check_volume_capabilities(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeCapabilities:
    """Compute host- and volume-level capabilities once per active volume."""
    has_rsync = _check_command_available(volume, "rsync", resolved_endpoints)
    rsync_version_ok = (
        _check_rsync_version(volume, resolved_endpoints) if has_rsync else False
    )
    has_btrfs = _check_command_available(volume, "btrfs", resolved_endpoints)
    has_stat = _check_command_available(volume, "stat", resolved_endpoints)
    has_findmnt = _check_command_available(volume, "findmnt", resolved_endpoints)
    is_btrfs = (
        _check_btrfs_filesystem(volume, resolved_endpoints) if has_stat else False
    )
    hardlink_supported = (
        _check_hardlink_support(volume, resolved_endpoints) if has_stat else True
    )
    btrfs_user_subvol_rm = (
        _check_btrfs_mount_option(volume, "user_subvol_rm_allowed", resolved_endpoints)
        if has_findmnt and is_btrfs
        else False
    )
    return VolumeCapabilities(
        has_rsync=has_rsync,
        rsync_version_ok=rsync_version_ok,
        has_btrfs=has_btrfs,
        has_stat=has_stat,
        has_findmnt=has_findmnt,
        is_btrfs_filesystem=is_btrfs,
        hardlink_supported=hardlink_supported,
        btrfs_user_subvol_rm=btrfs_user_subvol_rm,
    )


def _check_local_reachability(volume: LocalVolume) -> list[VolumeError]:
    """Check if a local volume is reachable (.nbkp-vol sentinel exists)."""
    sentinel = Path(volume.path) / VOLUME_SENTINEL
    return [] if sentinel.exists() else [VolumeError.SENTINEL_NOT_FOUND]


def _check_remote_reachability(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> list[VolumeError]:
    """Check if a remote volume is reachable (SSH + .nbkp-vol sentinel)."""
    if volume.slug not in resolved_endpoints:
        return [VolumeError.LOCATION_EXCLUDED]
    ep = resolved_endpoints[volume.slug]
    sentinel_path = f"{volume.path}/{VOLUME_SENTINEL}"
    try:
        result = run_remote_command(
            ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
        )
        return [] if result.returncode == 0 else [VolumeError.UNREACHABLE]
    except Exception:
        return [VolumeError.UNREACHABLE]
