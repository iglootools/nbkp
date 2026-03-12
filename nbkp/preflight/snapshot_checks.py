"""Snapshot, btrfs, hard-link, and symlink validation checks.

All functions return reasons rather than mutating a passed-in list.
"""

from __future__ import annotations

from ..config import (
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from ..sync.snapshots.common import DEVNULL_TARGET, LATEST_LINK, SNAPSHOTS_DIR
from .queries import (
    _check_directory_exists,
    _check_directory_writable,
    _check_symlink_exists,
    _read_symlink_target,
    _resolve_endpoint,
    _run_on_volume,
)
from .status import SyncReason


# ── Filesystem detection ────────────────────────────────────


def _check_btrfs_filesystem(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume path is on a btrfs filesystem."""
    result = _run_on_volume(
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
    result = _run_on_volume(
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
    result = _run_on_volume(["stat", "-c", "%i", path], volume, resolved_endpoints)
    return result.returncode == 0 and result.stdout.strip() == "256"


def _check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume is mounted with a specific mount option."""
    result = _run_on_volume(
        ["findmnt", "-T", volume.path, "-n", "-o", "OPTIONS"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0 and option in result.stdout.strip().split(",")


# ── Destination snapshot validation ─────────────────────────


def _check_btrfs_dest(
    dst_vol: Volume,
    dst_subdir: str | None,
    has_findmnt: bool,
    resolved_endpoints: ResolvedEndpoints,
) -> list[SyncReason]:
    """Run btrfs filesystem, subvolume, and directory checks."""
    reasons: list[SyncReason] = []
    if not _check_btrfs_filesystem(dst_vol, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_NOT_BTRFS)
    elif not _check_btrfs_subvolume(
        dst_vol,
        dst_subdir,
        resolved_endpoints,
    ):
        reasons.append(SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME)
    else:
        if has_findmnt and not _check_btrfs_mount_option(
            dst_vol,
            "user_subvol_rm_allowed",
            resolved_endpoints,
        ):
            reasons.append(SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM)
        ep = _resolve_endpoint(dst_vol, dst_subdir)
        from ..sync.snapshots.btrfs import STAGING_DIR

        staging_path = f"{ep}/{STAGING_DIR}"
        if not _check_directory_exists(dst_vol, staging_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_TMP_NOT_FOUND)
        elif not _check_directory_writable(dst_vol, staging_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_STAGING_DIR_NOT_WRITABLE)
        snaps_path = f"{ep}/{SNAPSHOTS_DIR}"
        if not _check_directory_exists(dst_vol, snaps_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)
        elif not _check_directory_writable(dst_vol, snaps_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE)
    return reasons


def _check_hard_link_dest(
    dst_vol: Volume,
    dst_subdir: str | None,
    resolved_endpoints: ResolvedEndpoints,
) -> list[SyncReason]:
    """Run hard-link snapshot filesystem and directory checks."""
    reasons: list[SyncReason] = []
    if not _check_hardlink_support(dst_vol, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_NO_HARDLINK_SUPPORT)
    ep = _resolve_endpoint(dst_vol, dst_subdir)
    snaps_path = f"{ep}/{SNAPSHOTS_DIR}"
    if not _check_directory_exists(dst_vol, snaps_path, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)
    elif not _check_directory_writable(dst_vol, snaps_path, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE)
    return reasons


# ── Latest symlink validation ───────────────────────────────


def _check_latest_symlink(
    volume: Volume,
    endpoint_path: str,
    not_found_reason: SyncReason,
    invalid_reason: SyncReason,
    resolved_endpoints: ResolvedEndpoints,
) -> tuple[str | None, list[SyncReason]]:
    """Validate the latest symlink at an endpoint.

    Checks that the symlink exists and points to either ``/dev/null``
    (valid "no snapshot yet" marker) or an existing relative snapshot
    directory.

    Returns a tuple of (snapshot_name_or_None, reasons).
    """
    reasons: list[SyncReason] = []
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    if not _check_symlink_exists(volume, latest_path, resolved_endpoints):
        reasons.append(not_found_reason)
        return None, reasons

    raw_target = _read_symlink_target(volume, latest_path, resolved_endpoints)
    if raw_target is None:
        reasons.append(not_found_reason)
        return None, reasons

    target = str(raw_target)
    if target == DEVNULL_TARGET:
        return None, reasons  # Valid "no snapshot yet" marker

    # Resolve relative target against endpoint path
    resolved = f"{endpoint_path}/{target}"
    if not _check_directory_exists(volume, resolved, resolved_endpoints):
        reasons.append(invalid_reason)
        return None, reasons

    # Extract snapshot name from relative target
    # e.g. "snapshots/2026-03-06T14:30:00.000Z" -> "2026-03-06T14:30:00.000Z"
    return target.rsplit("/", 1)[-1], reasons


# ── Source snapshot validation ──────────────────────────────


def _has_upstream_sync(
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
) -> bool:
    """Check if an enabled upstream sync writes to this sync's source.

    An upstream sync is one whose destination endpoint slug
    matches this sync's source endpoint slug.
    """
    return any(
        other.destination == sync.source and other.slug != sync.slug and other.enabled
        for other in all_syncs.values()
    )


def _check_source_latest(
    sync: SyncConfig,
    src_vol: Volume,
    endpoint_path: str,
    all_syncs: dict[str, SyncConfig],
    resolved_endpoints: ResolvedEndpoints,
    dry_run: bool = False,
) -> list[SyncReason]:
    """Validate the source latest symlink.

    ``/dev/null`` is accepted only when an enabled upstream sync writes
    to this source endpoint (it will populate the snapshot).

    In dry-run mode, ``/dev/null`` with an upstream sync marks the sync
    as inactive because the upstream dry-run won't create a real snapshot.
    """
    reasons: list[SyncReason] = []
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    if not _check_symlink_exists(src_vol, latest_path, resolved_endpoints):
        reasons.append(SyncReason.SOURCE_LATEST_NOT_FOUND)
        return reasons

    target = _read_symlink_target(src_vol, latest_path, resolved_endpoints)
    if target is None:
        reasons.append(SyncReason.SOURCE_LATEST_NOT_FOUND)
        return reasons

    if target == DEVNULL_TARGET:
        if not _has_upstream_sync(sync, all_syncs):
            reasons.append(SyncReason.SOURCE_LATEST_INVALID)
        elif dry_run:
            reasons.append(SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING)
        return reasons

    # Resolve relative target against endpoint path
    resolved = f"{endpoint_path}/{target}"
    if not _check_directory_exists(src_vol, resolved, resolved_endpoints):
        reasons.append(SyncReason.SOURCE_LATEST_INVALID)
    return reasons
