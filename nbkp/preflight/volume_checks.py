"""Volume observation: raw state and capabilities without error interpretation."""

from __future__ import annotations

from pathlib import Path

from ..config import (
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    Volume,
)
from ..fsprotocol import VOLUME_SENTINEL
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
from .status import VolumeCapabilities, VolumeDiagnostics


def observe_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeDiagnostics:
    """Observe volume state without interpreting errors.

    Returns a ``VolumeDiagnostics`` capturing raw observations:
    sentinel existence, SSH reachability, and capabilities.
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _observe_local(volume, re)
        case RemoteVolume():
            return _observe_remote(volume, re)


def _observe_local(
    volume: LocalVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeDiagnostics:
    """Observe a local volume's state."""
    sentinel_exists = (Path(volume.path) / VOLUME_SENTINEL).exists()
    caps = (
        check_volume_capabilities(volume, resolved_endpoints)
        if sentinel_exists
        else _sentinel_only_capabilities()
    )
    return VolumeDiagnostics(
        capabilities=caps,
    )


def _observe_remote(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeDiagnostics:
    """Observe a remote volume's state."""
    if volume.slug not in resolved_endpoints:
        return VolumeDiagnostics(location_excluded=True)
    else:
        ep = resolved_endpoints[volume.slug]
        sentinel_path = f"{volume.path}/{VOLUME_SENTINEL}"
        try:
            result = run_remote_command(
                ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
            )
            ssh_reachable = True
            sentinel_exists = result.returncode == 0
        except Exception:
            ssh_reachable = False
            sentinel_exists = None
        caps = (
            check_volume_capabilities(volume, resolved_endpoints)
            if sentinel_exists
            else _sentinel_only_capabilities()
            if ssh_reachable
            else None
        )
        return VolumeDiagnostics(
            location_excluded=False,
            ssh_reachable=ssh_reachable,
            capabilities=caps,
        )


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
        sentinel_exists=True,
        has_rsync=has_rsync,
        rsync_version_ok=rsync_version_ok,
        has_btrfs=has_btrfs,
        has_stat=has_stat,
        has_findmnt=has_findmnt,
        is_btrfs_filesystem=is_btrfs,
        hardlink_supported=hardlink_supported,
        btrfs_user_subvol_rm=btrfs_user_subvol_rm,
    )


def _sentinel_only_capabilities() -> VolumeCapabilities:
    """Minimal capabilities for a reachable volume whose sentinel is missing.

    Only ``sentinel_exists`` is meaningful; the remaining fields are
    not probed and carry safe defaults.
    """
    return VolumeCapabilities(
        sentinel_exists=False,
        has_rsync=False,
        rsync_version_ok=False,
        has_btrfs=False,
        has_stat=False,
        has_findmnt=False,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
    )
