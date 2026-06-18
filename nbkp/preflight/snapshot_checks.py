"""Snapshot, btrfs, hard-link, and mount-option checks.

Low-level queries used by ``volume_checks`` and ``endpoint_checks``.
"""

from __future__ import annotations

from ..config import Volume
from ..config.epresolution import ResolvedEndpoints
from ..remote.dispatch import run_on_volume
from ..remote.queries import (
    resolve_endpoint,
)


# ── Filesystem detection ────────────────────────────────────


def check_btrfs_filesystem(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume path is on a btrfs filesystem."""
    result = run_on_volume(
        ["stat", "-f", "-c", "%T", resolve_endpoint(volume, None)],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0 and result.stdout.strip() == "btrfs"


_NO_HARDLINK_FILESYSTEMS = {"vfat", "msdos", "exfat"}


def check_hardlink_support(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume filesystem supports hard links.

    Rejects known non-hardlink filesystems (FAT, exFAT).
    """
    result = run_on_volume(
        ["stat", "-f", "-c", "%T", resolve_endpoint(volume, None)],
        volume,
        resolved_endpoints,
    )
    return (
        result.returncode != 0  # Cannot determine; assume supported
        or result.stdout.strip() not in _NO_HARDLINK_FILESYSTEMS
    )


# ── Btrfs subvolume / mount option ─────────────────────────


def check_btrfs_subvolume(
    volume: Volume,
    subdir: str | None,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the endpoint path is a btrfs subvolume.

    On btrfs, subvolumes always have inode number 256.
    """
    path = resolve_endpoint(volume, subdir)
    result = run_on_volume(["stat", "-c", "%i", path], volume, resolved_endpoints)
    return result.returncode == 0 and result.stdout.strip() == "256"


def check_btrfs_readonly(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a btrfs subvolume is read-only."""
    result = run_on_volume(
        ["btrfs", "property", "get", path, "ro"], volume, resolved_endpoints
    )
    return result.returncode == 0 and "ro=true" in result.stdout


def check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume is mounted with a specific mount option."""
    # This is the one mount query udisks2 cannot answer, which is why findmnt
    # stays the tool for all of them. udisks exposes mountpoints
    # (Filesystem.MountPoints) and *configured* fstab/crypttab options
    # (Block.Configuration), but NOT the live mount-option string of a mounted
    # filesystem. Worse, Block.Configuration only reflects fstab-supplied options,
    # so an option granted via /etc/udisks2/mount_options.conf would be invisible
    # there — breaking this check's route-agnostic guarantee (it must detect
    # user_subvol_rm_allowed however it was supplied: fstab OR mount_options.conf).
    # findmnt reads /proc/self/mountinfo (the kernel's live truth), so it sees the
    # option regardless of how it got applied. (See find_mountpoint and
    # _check_fstab_entry, which udisks *could* answer but use findmnt for symmetry.)
    result = run_on_volume(
        ["findmnt", "-T", resolve_endpoint(volume, None), "-n", "-o", "OPTIONS"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0 and option in result.stdout.strip().split(",")
