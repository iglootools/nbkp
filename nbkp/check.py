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


class VolumeReason(str, enum.Enum):
    SENTINEL_NOT_FOUND = ".nbkp-vol volume sentinel not found"
    UNREACHABLE = "unreachable"


class SyncReason(str, enum.Enum):
    DISABLED = "disabled"
    SOURCE_UNAVAILABLE = "source unavailable"
    DESTINATION_UNAVAILABLE = "destination unavailable"
    SOURCE_SENTINEL_NOT_FOUND = ".nbkp-src source sentinel not found"
    DESTINATION_SENTINEL_NOT_FOUND = ".nbkp-dst destination sentinel not found"
    SOURCE_LATEST_NOT_FOUND = "source latest/ directory not found"
    SOURCE_SNAPSHOTS_DIR_NOT_FOUND = "source snapshots/ directory not found"
    RSYNC_NOT_FOUND_ON_SOURCE = "rsync not found on source"
    RSYNC_NOT_FOUND_ON_DESTINATION = "rsync not found on destination"
    RSYNC_TOO_OLD_ON_SOURCE = (
        "rsync too old on source (3.0+ required)"
    )
    RSYNC_TOO_OLD_ON_DESTINATION = (
        "rsync too old on destination (3.0+ required)"
    )
    BTRFS_NOT_FOUND_ON_DESTINATION = "btrfs not found on destination"
    STAT_NOT_FOUND_ON_DESTINATION = "stat not found on destination"
    FINDMNT_NOT_FOUND_ON_DESTINATION = "findmnt not found on destination"
    DESTINATION_NOT_BTRFS = "destination not on btrfs filesystem"
    DESTINATION_NOT_BTRFS_SUBVOLUME = (
        "destination endpoint is not a btrfs subvolume"
    )
    DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM = (
        "destination not mounted with user_subvol_rm_allowed"
    )
    DESTINATION_LATEST_NOT_FOUND = "destination latest/ directory not found"
    DESTINATION_SNAPSHOTS_DIR_NOT_FOUND = (
        "destination snapshots/ directory not found"
    )
    DESTINATION_NO_HARDLINK_SUPPORT = (
        "destination filesystem does not support hard links"
    )


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

    slug: str
    config: Volume
    reasons: list[VolumeReason]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return len(self.reasons) == 0


class SyncStatus(BaseModel):
    """Runtime status of a sync."""

    slug: str
    config: SyncConfig
    source_status: VolumeStatus
    destination_status: VolumeStatus
    reasons: list[SyncReason]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return len(self.reasons) == 0


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
    ep = resolved_endpoints[volume.slug]
    sentinel_path = f"{volume.path}/.nbkp-vol"
    result = run_remote_command(
        ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
    )
    reasons: list[VolumeReason] = (
        [] if result.returncode == 0 else [VolumeReason.UNREACHABLE]
    )
    return VolumeStatus(
        slug=volume.slug,
        config=volume,
        reasons=reasons,
    )


def _check_endpoint_sentinel(
    volume: Volume,
    subdir: str | None,
    sentinel_name: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if an endpoint sentinel file exists."""
    if subdir:
        rel_path = f"{volume.path}/{subdir}/{sentinel_name}"
    else:
        rel_path = f"{volume.path}/{sentinel_name}"

    match volume:
        case LocalVolume():
            return Path(rel_path).exists()
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(
                ep.server, ["test", "-f", rel_path], ep.proxy_chain
            )
            return result.returncode == 0


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
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(
                ep.server, ["which", command], ep.proxy_chain
            )
            return result.returncode == 0


_MIN_RSYNC_VERSION = (3, 0, 0)

_GNU_RSYNC_RE = regex.compile(
    r"rsync\s+version\s+(\d+)\.(\d+)\.(\d+)"
)


def parse_rsync_version(output: str) -> tuple[int, ...]:
    """Extract version tuple from ``rsync --version`` output.

    GNU rsync:  ``rsync  version 3.2.7  protocol version 31``
    openrsync:  ``openrsync: protocol version 29``

    Returns ``(0, 0, 0)`` for openrsync or unparseable output.
    """
    if "openrsync" in output:
        return (0, 0, 0)
    m = _GNU_RSYNC_RE.search(output)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)


def _check_rsync_version(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check that rsync is GNU rsync >= 3.0.0."""
    cmd = ["rsync", "--version"]
    match volume:
        case LocalVolume():
            result = subprocess.run(
                cmd, capture_output=True, text=True
            )
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(
                ep.server, cmd, ep.proxy_chain
            )
    if result.returncode != 0:
        return False
    return parse_rsync_version(result.stdout) >= _MIN_RSYNC_VERSION


def _check_btrfs_filesystem(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume path is on a btrfs filesystem."""
    cmd = ["stat", "-f", "-c", "%T", volume.path]
    match volume:
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
    return result.returncode == 0 and result.stdout.strip() == "btrfs"


_NO_HARDLINK_FILESYSTEMS = {"vfat", "msdos", "exfat"}


def _check_hardlink_support(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume filesystem supports hard links.

    Rejects known non-hardlink filesystems (FAT, exFAT).
    """
    cmd = ["stat", "-f", "-c", "%T", volume.path]
    match volume:
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
    if result.returncode != 0:
        return True  # Cannot determine; assume supported
    fs_type = result.stdout.strip()
    return fs_type not in _NO_HARDLINK_FILESYSTEMS


def _resolve_endpoint(volume: Volume, subdir: str | None) -> str:
    """Resolve the full endpoint path for a volume."""
    if subdir:
        return f"{volume.path}/{subdir}"
    else:
        return volume.path


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
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(
                ep.server, ["test", "-d", path], ep.proxy_chain
            )
            return result.returncode == 0


def _check_btrfs_subvolume(
    volume: Volume,
    subdir: str | None,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the endpoint path is a btrfs subvolume.

    On btrfs, subvolumes always have inode number 256.
    """
    path = _resolve_endpoint(volume, subdir)
    cmd = ["stat", "-c", "%i", path]
    match volume:
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
    return result.returncode == 0 and result.stdout.strip() == "256"


def _check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if the volume is mounted with a specific mount option."""
    cmd = ["findmnt", "-T", volume.path, "-n", "-o", "OPTIONS"]
    match volume:
        case LocalVolume():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            result = run_remote_command(ep.server, cmd, ep.proxy_chain)
    if result.returncode != 0:
        return False
    options = result.stdout.strip().split(",")
    return option in options


def _check_btrfs_dest(
    dst_vol: Volume,
    sync: SyncConfig,
    has_findmnt: bool,
    reasons: list[SyncReason],
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Run btrfs filesystem, subvolume, and directory checks."""
    if not _check_btrfs_filesystem(dst_vol, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_NOT_BTRFS)
    elif not _check_btrfs_subvolume(
        dst_vol,
        sync.destination.subdir,
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
        ep = _resolve_endpoint(dst_vol, sync.destination.subdir)
        if not _check_directory_exists(
            dst_vol, f"{ep}/latest", resolved_endpoints
        ):
            reasons.append(SyncReason.DESTINATION_LATEST_NOT_FOUND)
        if not _check_directory_exists(
            dst_vol, f"{ep}/snapshots", resolved_endpoints
        ):
            reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)


def _check_hard_link_dest(
    dst_vol: Volume,
    sync: SyncConfig,
    reasons: list[SyncReason],
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Run hard-link snapshot filesystem and directory checks."""
    if not _check_hardlink_support(dst_vol, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_NO_HARDLINK_SUPPORT)
    ep = _resolve_endpoint(dst_vol, sync.destination.subdir)
    if not _check_directory_exists(
        dst_vol, f"{ep}/snapshots", resolved_endpoints
    ):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)


def check_sync(
    sync: SyncConfig,
    config: Config,
    volume_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> SyncStatus:
    """Check if a sync is active, accumulating all failure reasons."""
    re = resolved_endpoints or {}
    src_vol_name = sync.source.volume
    dst_vol_name = sync.destination.volume

    src_status = volume_statuses[src_vol_name]
    dst_status = volume_statuses[dst_vol_name]

    if not sync.enabled:
        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            reasons=[SyncReason.DISABLED],
        )
    else:
        reasons: list[SyncReason] = []

        src_vol = config.volumes[src_vol_name]
        dst_vol = config.volumes[dst_vol_name]

        # Volume availability
        if not src_status.active:
            reasons.append(SyncReason.SOURCE_UNAVAILABLE)

        if not dst_status.active:
            reasons.append(SyncReason.DESTINATION_UNAVAILABLE)

        # Source checks (only if source volume is active)
        if src_status.active:
            if not _check_endpoint_sentinel(
                src_vol,
                sync.source.subdir,
                ".nbkp-src",
                re,
            ):
                reasons.append(SyncReason.SOURCE_SENTINEL_NOT_FOUND)
            if not _check_command_available(src_vol, "rsync", re):
                reasons.append(SyncReason.RSYNC_NOT_FOUND_ON_SOURCE)
            elif not _check_rsync_version(src_vol, re):
                reasons.append(SyncReason.RSYNC_TOO_OLD_ON_SOURCE)
            if sync.source.snapshot_mode != "none":
                src_ep = _resolve_endpoint(src_vol, sync.source.subdir)
                if not _check_directory_exists(
                    src_vol, f"{src_ep}/latest", re
                ):
                    reasons.append(SyncReason.SOURCE_LATEST_NOT_FOUND)
                if not _check_directory_exists(
                    src_vol, f"{src_ep}/snapshots", re
                ):
                    reasons.append(SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND)

        # Destination checks (only if destination volume is active)
        if dst_status.active:
            if not _check_endpoint_sentinel(
                dst_vol,
                sync.destination.subdir,
                ".nbkp-dst",
                re,
            ):
                reasons.append(SyncReason.DESTINATION_SENTINEL_NOT_FOUND)
            if not _check_command_available(dst_vol, "rsync", re):
                reasons.append(SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION)
            elif not _check_rsync_version(dst_vol, re):
                reasons.append(
                    SyncReason.RSYNC_TOO_OLD_ON_DESTINATION
                )
            if sync.destination.btrfs_snapshots.enabled:
                if not _check_command_available(dst_vol, "btrfs", re):
                    reasons.append(SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION)
                else:
                    has_stat = _check_command_available(dst_vol, "stat", re)
                    has_findmnt = _check_command_available(
                        dst_vol, "findmnt", re
                    )

                    if not has_stat:
                        reasons.append(
                            SyncReason.STAT_NOT_FOUND_ON_DESTINATION
                        )
                    if not has_findmnt:
                        reasons.append(
                            SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION
                        )

                    if has_stat:
                        _check_btrfs_dest(
                            dst_vol,
                            sync,
                            has_findmnt,
                            reasons,
                            re,
                        )
            elif sync.destination.hard_link_snapshots.enabled:
                has_stat = _check_command_available(dst_vol, "stat", re)
                if not has_stat:
                    reasons.append(SyncReason.STAT_NOT_FOUND_ON_DESTINATION)
                else:
                    _check_hard_link_dest(dst_vol, sync, reasons, re)

        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            reasons=reasons,
        )


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
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
        {sc.source.volume for sc in syncs.values()}
        | {sc.destination.volume for sc in syncs.values()}
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
        sync_statuses[slug] = check_sync(sync, config, volume_statuses, re)
        if on_progress:
            on_progress(slug)

    return volume_statuses, sync_statuses
