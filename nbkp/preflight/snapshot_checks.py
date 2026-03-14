"""Snapshot, btrfs, hard-link, and mount-option checks.

Low-level queries used by ``volume_checks`` and ``endpoint_checks``.
"""

from __future__ import annotations

from ..config import (
    ResolvedEndpoints,
    Volume,
)
from ..remote.dispatch import run_on_volume
from .queries import (
    _resolve_endpoint,
)


# ── Filesystem detection ────────────────────────────────────


def _check_btrfs_filesystem(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume path is on a btrfs filesystem."""
    result = run_on_volume(
        ["stat", "-f", "-c", "%T", volume.path], volume, resolved_endpoints
    )
    return result.returncode == 0 and result.stdout.strip() == "btrfs"


_NO_HARDLINK_FILESYSTEMS = {"vfat", "msdos", "exfat"}


def _check_hardlink_support(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume filesystem supports hard links.

    Rejects known non-hardlink filesystems (FAT, exFAT).
    """
    result = run_on_volume(
        ["stat", "-f", "-c", "%T", volume.path], volume, resolved_endpoints
    )
    return (
        result.returncode != 0  # Cannot determine; assume supported
        or result.stdout.strip() not in _NO_HARDLINK_FILESYSTEMS
    )


# ── Btrfs subvolume / mount option ─────────────────────────


def _check_btrfs_subvolume(
    volume: Volume,
    subdir: str | None,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the endpoint path is a btrfs subvolume.

    On btrfs, subvolumes always have inode number 256.
    """
    path = _resolve_endpoint(volume, subdir)
    result = run_on_volume(["stat", "-c", "%i", path], volume, resolved_endpoints)
    return result.returncode == 0 and result.stdout.strip() == "256"


def _check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume is mounted with a specific mount option."""
    result = run_on_volume(
        ["findmnt", "-T", volume.path, "-n", "-o", "OPTIONS"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0 and option in result.stdout.strip().split(",")
