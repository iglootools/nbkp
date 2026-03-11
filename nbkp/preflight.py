"""Runtime status types for volumes and syncs, and activity checks."""

from __future__ import annotations

import enum
import re as regex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, computed_field

from .config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
    Volume,
)
from .remote import run_remote_command
from .sync.snapshots.btrfs import STAGING_DIR
from .sync.snapshots.common import DEVNULL_TARGET, LATEST_LINK, SNAPSHOTS_DIR


class VolumeReason(str, enum.Enum):
    SENTINEL_NOT_FOUND = ".nbkp-vol volume sentinel not found"
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"


class SyncReason(str, enum.Enum):
    DISABLED = "disabled"
    SOURCE_UNAVAILABLE = "source unavailable"
    DESTINATION_UNAVAILABLE = "destination unavailable"
    SOURCE_SENTINEL_NOT_FOUND = ".nbkp-src source sentinel not found"
    DESTINATION_SENTINEL_NOT_FOUND = ".nbkp-dst destination sentinel not found"
    SOURCE_LATEST_NOT_FOUND = f"source {LATEST_LINK} symlink not found"
    SOURCE_LATEST_INVALID = f"source {LATEST_LINK} symlink target is invalid"
    SOURCE_SNAPSHOTS_DIR_NOT_FOUND = f"source {SNAPSHOTS_DIR}/ directory not found"
    RSYNC_NOT_FOUND_ON_SOURCE = "rsync not found on source"
    RSYNC_NOT_FOUND_ON_DESTINATION = "rsync not found on destination"
    RSYNC_TOO_OLD_ON_SOURCE = "rsync too old on source (3.0+ required)"
    RSYNC_TOO_OLD_ON_DESTINATION = "rsync too old on destination (3.0+ required)"
    BTRFS_NOT_FOUND_ON_DESTINATION = "btrfs not found on destination"
    STAT_NOT_FOUND_ON_DESTINATION = "stat not found on destination"
    FINDMNT_NOT_FOUND_ON_DESTINATION = "findmnt not found on destination"
    DESTINATION_NOT_BTRFS = "destination not on btrfs filesystem"
    DESTINATION_NOT_BTRFS_SUBVOLUME = "destination endpoint is not a btrfs subvolume"
    DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM = (
        "destination not mounted with user_subvol_rm_allowed"
    )
    DESTINATION_TMP_NOT_FOUND = f"destination {STAGING_DIR}/ directory not found"
    DESTINATION_SNAPSHOTS_DIR_NOT_FOUND = (
        f"destination {SNAPSHOTS_DIR}/ directory not found"
    )
    DESTINATION_LATEST_NOT_FOUND = f"destination {LATEST_LINK} symlink not found"
    DESTINATION_LATEST_INVALID = f"destination {LATEST_LINK} symlink target is invalid"
    DESTINATION_NO_HARDLINK_SUPPORT = (
        "destination filesystem does not support hard links"
    )
    DRY_RUN_SOURCE_SNAPSHOT_PENDING = (
        "source snapshot not yet available (dry-run; upstream has not run)"
    )


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

    slug: str
    config: Volume
    reasons: list[VolumeReason]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.reasons


class SyncStatus(BaseModel):
    """Runtime status of a sync."""

    slug: str
    config: SyncConfig
    source_status: VolumeStatus
    destination_status: VolumeStatus
    reasons: list[SyncReason]
    destination_latest_target: str | None = None
    """Snapshot name from the destination ``latest`` symlink.

    ``None`` when the symlink is absent, invalid, or points to
    ``/dev/null`` (no snapshot yet).  Otherwise, the snapshot
    name only (e.g. ``2026-03-06T14:30:00.000Z``).
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.reasons


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeStatus:
    """Check if a volume is active."""
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _check_local_volume(volume)
        case RemoteVolume():
            return _check_remote_volume(volume, re)


def _check_local_volume(volume: LocalVolume) -> VolumeStatus:
    """Check if a local volume is active (.nbkp-vol sentinel exists)."""
    sentinel = Path(volume.path) / ".nbkp-vol"
    reasons: list[VolumeReason] = (
        [] if sentinel.exists() else [VolumeReason.SENTINEL_NOT_FOUND]
    )
    return VolumeStatus(
        slug=volume.slug,
        config=volume,
        reasons=reasons,
    )


def _check_remote_volume(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeStatus:
    """Check if a remote volume is active (SSH + .nbkp-vol sentinel)."""
    if volume.slug not in resolved_endpoints:
        return VolumeStatus(
            slug=volume.slug,
            config=volume,
            reasons=[VolumeReason.LOCATION_EXCLUDED],
        )
    ep = resolved_endpoints[volume.slug]
    sentinel_path = f"{volume.path}/.nbkp-vol"
    try:
        result = run_remote_command(
            ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
        )
        reasons: list[VolumeReason] = (
            [] if result.returncode == 0 else [VolumeReason.UNREACHABLE]
        )
    except Exception:
        reasons = [VolumeReason.UNREACHABLE]
    return VolumeStatus(
        slug=volume.slug,
        config=volume,
        reasons=reasons,
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


def _check_endpoint_sentinel(
    volume: Volume,
    subdir: str | None,
    sentinel_name: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if an endpoint sentinel file exists."""
    rel_path = (
        f"{volume.path}/{subdir}/{sentinel_name}"
        if subdir
        else f"{volume.path}/{sentinel_name}"
    )
    match volume:
        case LocalVolume():
            return Path(rel_path).exists()
        case RemoteVolume():
            return _run_on_volume(
                ["test", "-f", rel_path], volume, resolved_endpoints
            ).returncode == 0


def _check_command_available(
    volume: Volume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a command is available on the volume's host."""
    match volume:
        case LocalVolume():
            return shutil.which(command) is not None
        case RemoteVolume():
            return _run_on_volume(
                ["which", command], volume, resolved_endpoints
            ).returncode == 0


_MIN_RSYNC_VERSION = (3, 0, 0)

_GNU_RSYNC_RE = regex.compile(r"rsync\s+version\s+(\d+)\.(\d+)\.(\d+)")


def parse_rsync_version(output: str) -> tuple[int, ...]:
    """Extract version tuple from ``rsync --version`` output.

    GNU rsync:  ``rsync  version 3.2.7  protocol version 31``
    openrsync:  ``openrsync: protocol version 29``

    Returns ``(0, 0, 0)`` for openrsync or unparseable output.
    """
    m = _GNU_RSYNC_RE.search(output) if "openrsync" not in output else None
    return (
        (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m
        else (0, 0, 0)
    )


def _check_rsync_version(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check that rsync is GNU rsync >= 3.0.0."""
    result = _run_on_volume(["rsync", "--version"], volume, resolved_endpoints)
    return (
        result.returncode == 0
        and parse_rsync_version(result.stdout) >= _MIN_RSYNC_VERSION
    )


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


def _resolve_endpoint(volume: Volume, subdir: str | None) -> str:
    """Resolve the full endpoint path for a volume."""
    return f"{volume.path}/{subdir}" if subdir else volume.path


def _check_directory_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a directory exists on the volume's host."""
    match volume:
        case LocalVolume():
            return Path(path).is_dir()
        case RemoteVolume():
            return _run_on_volume(
                ["test", "-d", path], volume, resolved_endpoints
            ).returncode == 0


def _check_symlink_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a symlink exists on the volume's host."""
    match volume:
        case LocalVolume():
            return Path(path).is_symlink()
        case RemoteVolume():
            return _run_on_volume(
                ["test", "-L", path], volume, resolved_endpoints
            ).returncode == 0


def _read_symlink_target(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Read a symlink target, returning None if it doesn't exist."""
    match volume:
        case LocalVolume():
            p = Path(path)
            return str(p.readlink()) if p.is_symlink() else None
        case RemoteVolume():
            result = _run_on_volume(
                ["readlink", path], volume, resolved_endpoints
            )
            return result.stdout.strip() if result.returncode == 0 else None


def _check_latest_symlink(
    volume: Volume,
    endpoint_path: str,
    reasons: list[SyncReason],
    not_found_reason: SyncReason,
    invalid_reason: SyncReason,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Validate the latest symlink at an endpoint.

    Checks that the symlink exists and points to either ``/dev/null``
    (valid "no snapshot yet" marker) or an existing relative snapshot
    directory.

    Returns the raw symlink target when valid and not ``/dev/null``,
    or ``None`` otherwise.
    """
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    if not _check_symlink_exists(volume, latest_path, resolved_endpoints):
        reasons.append(not_found_reason)
        return None

    raw_target = _read_symlink_target(volume, latest_path, resolved_endpoints)
    if raw_target is None:
        reasons.append(not_found_reason)
        return None

    target = str(raw_target)
    if target == DEVNULL_TARGET:
        return None  # Valid "no snapshot yet" marker

    # Resolve relative target against endpoint path
    resolved = f"{endpoint_path}/{target}"
    if not _check_directory_exists(volume, resolved, resolved_endpoints):
        reasons.append(invalid_reason)
        return None

    # Extract snapshot name from relative target
    # e.g. "snapshots/2026-03-06T14:30:00.000Z" -> "2026-03-06T14:30:00.000Z"
    return target.rsplit("/", 1)[-1]


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


def _check_btrfs_dest(
    dst_vol: Volume,
    dst_subdir: str | None,
    has_findmnt: bool,
    reasons: list[SyncReason],
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Run btrfs filesystem, subvolume, and directory checks."""
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
        if not _check_directory_exists(
            dst_vol, f"{ep}/{STAGING_DIR}", resolved_endpoints
        ):
            reasons.append(SyncReason.DESTINATION_TMP_NOT_FOUND)
        if not _check_directory_exists(
            dst_vol,
            f"{ep}/{SNAPSHOTS_DIR}",
            resolved_endpoints,
        ):
            reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)


def _check_hard_link_dest(
    dst_vol: Volume,
    dst_subdir: str | None,
    reasons: list[SyncReason],
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Run hard-link snapshot filesystem and directory checks."""
    if not _check_hardlink_support(dst_vol, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_NO_HARDLINK_SUPPORT)
    ep = _resolve_endpoint(dst_vol, dst_subdir)
    if not _check_directory_exists(
        dst_vol, f"{ep}/{SNAPSHOTS_DIR}", resolved_endpoints
    ):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)


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
    reasons: list[SyncReason],
    resolved_endpoints: ResolvedEndpoints,
    dry_run: bool = False,
) -> None:
    """Validate the source latest symlink.

    ``/dev/null`` is accepted only when an enabled upstream sync writes
    to this source endpoint (it will populate the snapshot).

    In dry-run mode, ``/dev/null`` with an upstream sync marks the sync
    as inactive because the upstream dry-run won't create a real snapshot.
    """
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    if not _check_symlink_exists(src_vol, latest_path, resolved_endpoints):
        reasons.append(SyncReason.SOURCE_LATEST_NOT_FOUND)
        return

    target = _read_symlink_target(src_vol, latest_path, resolved_endpoints)
    if target is None:
        reasons.append(SyncReason.SOURCE_LATEST_NOT_FOUND)
        return

    if target == DEVNULL_TARGET:
        if not _has_upstream_sync(sync, all_syncs):
            reasons.append(SyncReason.SOURCE_LATEST_INVALID)
        elif dry_run:
            reasons.append(SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING)
        return

    # Resolve relative target against endpoint path
    resolved = f"{endpoint_path}/{target}"
    if not _check_directory_exists(src_vol, resolved, resolved_endpoints):
        reasons.append(SyncReason.SOURCE_LATEST_INVALID)


def check_sync(
    sync: SyncConfig,
    config: Config,
    volume_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints | None = None,
    all_syncs: dict[str, SyncConfig] | None = None,
    dry_run: bool = False,
) -> SyncStatus:
    """Check if a sync is active, accumulating all failure reasons."""
    re = resolved_endpoints or {}
    syncs = all_syncs if all_syncs is not None else config.syncs
    src_cfg = config.source_endpoint(sync)
    dst_cfg = config.destination_endpoint(sync)
    src_vol = config.volumes[src_cfg.volume]
    dst_vol = config.volumes[dst_cfg.volume]

    src_status = volume_statuses[src_cfg.volume]
    dst_status = volume_statuses[dst_cfg.volume]

    if not sync.enabled:
        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            reasons=[SyncReason.DISABLED],
        )

    reasons: list[SyncReason] = []
    dst_latest_target: str | None = None

    # Volume availability
    if not src_status.active:
        reasons.append(SyncReason.SOURCE_UNAVAILABLE)

    if not dst_status.active:
        reasons.append(SyncReason.DESTINATION_UNAVAILABLE)

    # Source checks (only if source volume is active)
    if src_status.active:
        if not _check_endpoint_sentinel(
            src_vol,
            src_cfg.subdir,
            ".nbkp-src",
            re,
        ):
            reasons.append(SyncReason.SOURCE_SENTINEL_NOT_FOUND)
        if not _check_command_available(src_vol, "rsync", re):
            reasons.append(SyncReason.RSYNC_NOT_FOUND_ON_SOURCE)
        elif not _check_rsync_version(src_vol, re):
            reasons.append(SyncReason.RSYNC_TOO_OLD_ON_SOURCE)
        if src_cfg.snapshot_mode != "none":
            src_ep = _resolve_endpoint(src_vol, src_cfg.subdir)
            _check_source_latest(
                sync, src_vol, src_ep, syncs, reasons, re, dry_run=dry_run
            )
            if not _check_directory_exists(
                src_vol, f"{src_ep}/{SNAPSHOTS_DIR}", re
            ):
                reasons.append(SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND)

    # Destination checks (only if dest volume is active)
    if dst_status.active:
        if not _check_endpoint_sentinel(
            dst_vol,
            dst_cfg.subdir,
            ".nbkp-dst",
            re,
        ):
            reasons.append(SyncReason.DESTINATION_SENTINEL_NOT_FOUND)
        if not _check_command_available(dst_vol, "rsync", re):
            reasons.append(SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION)
        elif not _check_rsync_version(dst_vol, re):
            reasons.append(SyncReason.RSYNC_TOO_OLD_ON_DESTINATION)
        if dst_cfg.btrfs_snapshots.enabled:
            if not _check_command_available(dst_vol, "btrfs", re):
                reasons.append(SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION)
            else:
                has_stat = _check_command_available(dst_vol, "stat", re)
                has_findmnt = _check_command_available(dst_vol, "findmnt", re)

                if not has_stat:
                    reasons.append(SyncReason.STAT_NOT_FOUND_ON_DESTINATION)
                if not has_findmnt:
                    reasons.append(SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION)

                if has_stat:
                    _check_btrfs_dest(
                        dst_vol,
                        dst_cfg.subdir,
                        has_findmnt,
                        reasons,
                        re,
                    )
        elif dst_cfg.hard_link_snapshots.enabled:
            has_stat = _check_command_available(dst_vol, "stat", re)
            if not has_stat:
                reasons.append(SyncReason.STAT_NOT_FOUND_ON_DESTINATION)
            else:
                _check_hard_link_dest(
                    dst_vol,
                    dst_cfg.subdir,
                    reasons,
                    re,
                )

        # Destination latest symlink check (snapshot modes)
        if dst_cfg.snapshot_mode != "none":
            dst_ep = _resolve_endpoint(dst_vol, dst_cfg.subdir)
            dst_latest_target = _check_latest_symlink(
                dst_vol,
                dst_ep,
                reasons,
                SyncReason.DESTINATION_LATEST_NOT_FOUND,
                SyncReason.DESTINATION_LATEST_INVALID,
                re,
            )

    return SyncStatus(
        slug=sync.slug,
        config=sync,
        source_status=src_status,
        destination_status=dst_status,
        reasons=reasons,
        destination_latest_target=dst_latest_target,
    )


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Check volumes and syncs, caching volume checks.

    When *only_syncs* is given, only those syncs (and the
    volumes they reference) are checked.
    """
    re = resolved_endpoints or {}
    syncs = (
        {s: sc for s, sc in config.syncs.items() if s in only_syncs}
        if only_syncs
        else config.syncs
    )

    needed_volumes: set[str] = (
        {config.source_endpoint(sc).volume for sc in syncs.values()}
        | {config.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(config.volumes.keys())
    )

    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        volume = config.volumes[slug]
        volume_statuses[slug] = check_volume(volume, re)
        if on_progress:
            on_progress(slug)

    sync_statuses: dict[str, SyncStatus] = {}
    for slug, sync in syncs.items():
        sync_statuses[slug] = check_sync(
            sync, config, volume_statuses, re, config.syncs, dry_run=dry_run
        )
        if on_progress:
            on_progress(slug)

    return volume_statuses, sync_statuses
