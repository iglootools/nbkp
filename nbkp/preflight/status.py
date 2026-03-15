"""Runtime status types for volumes and syncs."""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from ..config import (
    MountConfig,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
    Snapshot,
)


class VolumeError(str, enum.Enum):
    # Network management
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"

    # Mount management — runtime state
    VOLUME_NOT_MOUNTED = "volume not mounted"

    # Sentinel management
    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"

    # Mount management — lifecycle errors
    DEVICE_NOT_PRESENT = "device not plugged in"
    ATTACH_LUKS_FAILED = "failed to attach luks encrypted device"
    MOUNT_FAILED = "failed to mount volume"
    SYSTEMCTL_NOT_FOUND = "systemctl not found"
    SYSTEMD_ESCAPE_NOT_FOUND = "systemd-escape not found"
    MOUNT_UNIT_NOT_CONFIGURED = "mount unit not configured in systemd"
    MOUNT_UNIT_MISMATCH = "mount unit config does not match nbkp config"
    CRYPTSETUP_SERVICE_NOT_CONFIGURED = "cryptsetup service not configured in systemd"
    CRYPTSETUP_SERVICE_MISMATCH = "cryptsetup service config does not match nbkp config"
    POLKIT_RULES_MISSING = "polkit rules not configured"
    SUDOERS_RULES_MISSING = "sudoers rules not configured"
    SUDO_NOT_FOUND = "sudo not found"
    CRYPTSETUP_NOT_FOUND = "cryptsetup not found"
    SYSTEMD_CRYPTSETUP_NOT_FOUND = "systemd-cryptsetup not found"
    MOUNT_CMD_NOT_FOUND = "mount command not found"
    UMOUNT_CMD_NOT_FOUND = "umount command not found"
    MOUNTPOINT_CMD_NOT_FOUND = "mountpoint command not found"
    PASSPHRASE_NOT_AVAILABLE = "passphrase not available"


class SyncError(str, enum.Enum):
    DISABLED = "disabled"

    # Source Volume
    SRC_VOL_UNAVAILABLE = "source volume unavailable"
    SRC_VOL_RSYNC_NOT_FOUND = "rsync not found on source"
    SRC_VOL_RSYNC_TOO_OLD = "rsync too old on source (3.0+ required)"

    # Source Endpoint
    SRC_EP_SENTINEL_NOT_FOUND = f"source endpoint {SOURCE_SENTINEL} sentinel not found"
    SRC_EP_LATEST_SYMLINK_NOT_FOUND = f"source endpoint {LATEST_LINK} symlink not found"
    SRC_EP_LATEST_SYMLINK_INVALID = (
        f"source endpoint {LATEST_LINK} symlink target is invalid"
    )
    SRC_EP_SNAPSHOTS_DIR_NOT_FOUND = (
        f"source endpoint {SNAPSHOTS_DIR}/ directory not found"
    )

    # Destination Volume
    DST_VOL_UNAVAILABLE = "destination volume unavailable"

    DST_VOL_RSYNC_NOT_FOUND = "rsync not found on destination"
    DST_VOL_RSYNC_TOO_OLD = "rsync too old on destination (3.0+ required)"
    DST_VOL_BTRFS_NOT_FOUND = "btrfs not found on destination"
    DST_VOL_STAT_NOT_FOUND = "stat not found on destination"
    DST_VOL_FINDMNT_NOT_FOUND = "findmnt not found on destination"

    DST_VOL_NOT_BTRFS = "destination volume not on btrfs filesystem"
    DST_VOL_NOT_MOUNTED_USER_SUBVOL_RM = (
        "destination volume not mounted with user_subvol_rm_allowed"
    )
    DST_VOL_NO_HARDLINK_SUPPORT = (
        "destination volume filesystem does not support hard links"
    )

    # Destinatin Endpoint
    DST_EP_SENTINEL_NOT_FOUND = (
        f"destination endpoint {DESTINATION_SENTINEL} sentinel not found"
    )
    DST_EP_STAGING_SUBVOL_NOT_FOUND = (
        f"destination endpoint {STAGING_DIR}/ directory not found"
    )
    DST_EP_SNAPSHOTS_DIR_NOT_FOUND = (
        f"destination endpoint {SNAPSHOTS_DIR}/ directory not found"
    )
    DST_EP_LATEST_SYMLINK_NOT_FOUND = (
        f"destination endpoint {LATEST_LINK} symlink not found"
    )
    DST_EP_LATEST_SYMLINK_INVALID = (
        f"destination endpoint {LATEST_LINK} symlink target is invalid"
    )
    DST_EP_NOT_WRITABLE = "destination endpoint directory not writable"
    DST_EP_SNAPSHOTS_DIR_NOT_WRITABLE = (
        f"destination endpoint {SNAPSHOTS_DIR}/ directory not writable"
    )
    DST_EP_STAGING_NOT_BTRFS_SUBVOLUME = (
        f"destination endpoint {STAGING_DIR}/ is not a btrfs subvolume"
    )
    DST_EP_STAGING_SUBVOL_NOT_WRITABLE = (
        f"destination endpoint {STAGING_DIR}/ subvolume not writable"
    )

    DRY_RUN_SRC_EP_SNAPSHOT_PENDING = (
        "source snapshot not yet available (dry-run; upstream has not run)"
    )


class MountCapabilities(BaseModel):
    """Mount management capabilities, probed when a volume has mount config.

    Composed into ``VolumeCapabilities`` as an optional field.
    """

    model_config = ConfigDict(frozen=True)

    resolved_backend: Literal["systemd", "direct"] | None = None
    """Which backend was resolved (``None`` when auto-detection was not performed)."""

    # Systemd-specific (None when direct)
    has_systemctl: bool | None = None
    has_systemd_escape: bool | None = None
    mount_unit: str | None = None
    has_mount_unit_config: bool | None = None
    mount_unit_what: str | None = None
    mount_unit_where: str | None = None
    has_cryptsetup_service_config: bool | None = None
    cryptsetup_service_exec_start: str | None = None
    has_systemd_cryptsetup: bool | None = None
    systemd_cryptsetup_path: str | None = None
    has_polkit_rules: bool | None = None

    # Shared (both backends)
    has_sudo: bool | None = None
    has_cryptsetup: bool | None = None
    has_sudoers_rules: bool | None = None

    # Direct-specific (None when systemd)
    has_mount_cmd: bool | None = None
    has_umount_cmd: bool | None = None
    has_mountpoint: bool | None = None

    # Runtime mount state (probed during observation)
    device_present: bool | None = None
    luks_attached: bool | None = None
    mounted: bool | None = None


class VolumeCapabilities(BaseModel):
    """Host- and volume-level capabilities, computed once per reachable volume.

    Always created when the volume is reachable (locally or via SSH).
    When ``sentinel_exists`` is ``False``, the remaining fields are
    not probed and carry defaults — they are only meaningful when the
    sentinel is present.
    """

    model_config = ConfigDict(frozen=True)

    sentinel_exists: bool
    has_rsync: bool
    rsync_version_ok: bool
    has_btrfs: bool
    has_stat: bool
    has_findmnt: bool
    is_btrfs_filesystem: bool
    hardlink_supported: bool
    btrfs_user_subvol_rm: bool
    mount: MountCapabilities | None = None


class VolumeDiagnostics(BaseModel):
    """Observed state of a volume.

    Pure diagnostics — no interpretation of what constitutes an error.
    The check layer translates these into ``VolumeError`` values.
    """

    model_config = ConfigDict(frozen=True)

    location_excluded: bool = False
    """Whether all SSH endpoints for this volume were excluded by location filter."""
    ssh_reachable: bool | None = None
    """Whether the SSH endpoint is reachable.
    ``None`` for local volumes (not applicable)."""
    capabilities: VolumeCapabilities | None = None
    """Host- and volume-level capabilities.
    ``None`` when the volume is not reachable (location excluded or SSH unreachable)."""


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

    slug: str
    config: Volume
    diagnostics: VolumeDiagnostics
    errors: list[VolumeError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        config: Volume,
        diagnostics: VolumeDiagnostics,
    ) -> "VolumeStatus":
        """Create a ``VolumeStatus`` by interpreting diagnostics into errors."""
        mount = getattr(config, "mount", None)
        return VolumeStatus(
            slug=slug,
            config=config,
            diagnostics=diagnostics,
            errors=_volume_errors(diagnostics, mount),
        )


def _volume_errors(
    diag: VolumeDiagnostics,
    mount: MountConfig | None = None,
) -> list[VolumeError]:
    """Translate volume diagnostics into VolumeError values."""
    if diag.location_excluded:
        return [VolumeError.LOCATION_EXCLUDED]
    elif diag.ssh_reachable is False:
        return [VolumeError.UNREACHABLE]
    elif diag.capabilities is not None and not diag.capabilities.sentinel_exists:
        # When a volume has mount config and is not mounted, report
        # "not mounted" instead of the misleading "sentinel not found" —
        # the sentinel can only exist once the volume is mounted.
        mount_caps = diag.capabilities.mount
        if mount is not None and mount_caps is not None and mount_caps.mounted is False:
            return [VolumeError.VOLUME_NOT_MOUNTED]
        else:
            return [VolumeError.SENTINEL_NOT_FOUND]
    elif diag.capabilities is not None and mount is not None:
        return _mount_errors(diag.capabilities.mount, mount)
    else:
        return []


def _mount_errors(
    mount_caps: MountCapabilities | None,
    mount: MountConfig,
) -> list[VolumeError]:
    """Translate mount-related capabilities into VolumeError values."""
    if mount_caps is None:
        return []
    match mount_caps.resolved_backend:
        case "direct":
            return _direct_mount_errors(mount_caps, mount)
        case _:
            # "systemd" or None (legacy/unresolved) — use systemd checks
            return _systemd_mount_errors(mount_caps, mount)


def _systemd_mount_errors(
    mount_caps: MountCapabilities,
    mount: MountConfig,
) -> list[VolumeError]:
    """Translate systemd-specific mount capabilities into VolumeError values."""
    has_encryption = mount.encryption is not None
    return [
        *(
            [VolumeError.SYSTEMCTL_NOT_FOUND]
            if mount_caps.has_systemctl is False
            else []
        ),
        *(
            [VolumeError.SYSTEMD_ESCAPE_NOT_FOUND]
            if mount_caps.has_systemd_escape is False
            else []
        ),
        *(
            [VolumeError.MOUNT_UNIT_NOT_CONFIGURED]
            if mount_caps.has_mount_unit_config is False
            else []
        ),
        *(
            [VolumeError.MOUNT_UNIT_MISMATCH]
            if mount_caps.has_mount_unit_config is True
            and _mount_unit_mismatches(mount_caps, mount)
            else []
        ),
        *(
            [VolumeError.SUDO_NOT_FOUND]
            if has_encryption and mount_caps.has_sudo is False
            else []
        ),
        *(
            [VolumeError.CRYPTSETUP_NOT_FOUND]
            if has_encryption and mount_caps.has_cryptsetup is False
            else []
        ),
        *(
            [VolumeError.SYSTEMD_CRYPTSETUP_NOT_FOUND]
            if has_encryption and mount_caps.has_systemd_cryptsetup is False
            else []
        ),
        *(
            [VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED]
            if has_encryption and mount_caps.has_cryptsetup_service_config is False
            else []
        ),
        *(
            [VolumeError.CRYPTSETUP_SERVICE_MISMATCH]
            if has_encryption
            and mount_caps.has_cryptsetup_service_config is True
            and _cryptsetup_service_mismatches(mount_caps, mount)
            else []
        ),
        *(
            [VolumeError.POLKIT_RULES_MISSING]
            if mount_caps.has_polkit_rules is False
            else []
        ),
        *(
            [VolumeError.SUDOERS_RULES_MISSING]
            if has_encryption and mount_caps.has_sudoers_rules is False
            else []
        ),
    ]


def _direct_mount_errors(
    mount_caps: MountCapabilities,
    mount: MountConfig,
) -> list[VolumeError]:
    """Translate direct-backend mount capabilities into VolumeError values."""
    has_encryption = mount.encryption is not None
    return [
        *([VolumeError.SUDO_NOT_FOUND] if mount_caps.has_sudo is False else []),
        *(
            [VolumeError.MOUNT_CMD_NOT_FOUND]
            if mount_caps.has_mount_cmd is False
            else []
        ),
        *(
            [VolumeError.UMOUNT_CMD_NOT_FOUND]
            if mount_caps.has_umount_cmd is False
            else []
        ),
        *(
            [VolumeError.MOUNTPOINT_CMD_NOT_FOUND]
            if mount_caps.has_mountpoint is False
            else []
        ),
        *(
            [VolumeError.CRYPTSETUP_NOT_FOUND]
            if has_encryption and mount_caps.has_cryptsetup is False
            else []
        ),
        *(
            [VolumeError.SUDOERS_RULES_MISSING]
            if has_encryption and mount_caps.has_sudoers_rules is False
            else []
        ),
    ]


def _mount_unit_mismatches(
    mount_caps: MountCapabilities,
    mount: MountConfig,
) -> bool:
    """Check if the systemd mount unit config doesn't match expectations."""
    if mount.encryption is not None:
        expected_what = f"/dev/mapper/{mount.encryption.mapper_name}"
    else:
        expected_what = f"/dev/disk/by-uuid/{mount.device_uuid}"
    return (
        mount_caps.mount_unit_what is not None
        and mount_caps.mount_unit_what != expected_what
    )


def _cryptsetup_service_mismatches(
    mount_caps: MountCapabilities,
    mount: MountConfig,
) -> bool:
    """Check if the systemd cryptsetup service config doesn't match expectations."""
    if mount.encryption is None or mount_caps.cryptsetup_service_exec_start is None:
        return False
    exec_start = mount_caps.cryptsetup_service_exec_start
    return (
        mount.encryption.mapper_name not in exec_start
        or mount.device_uuid not in exec_start
    )


class LatestSymlinkState(BaseModel):
    """Observed state of a ``latest`` symlink at an endpoint.

    Captures the raw readlink result and whether the resolved
    target directory exists, without interpreting what constitutes
    an error.
    """

    model_config = ConfigDict(frozen=True)

    exists: bool
    raw_target: str | None = None
    """Raw readlink value.  ``/dev/null``, a relative path, or ``None``
    when the symlink is absent or unreadable."""
    target_valid: bool | None = None
    """Whether the resolved target directory exists.
    ``None`` when there is no symlink or target is ``/dev/null``."""
    snapshot: Snapshot | None = None
    """Snapshot extracted from the target path.
    ``None`` when target is ``/dev/null``, invalid, or absent."""


class BtrfsStagingSubvolumeDiagnostics(BaseModel):
    """Btrfs staging subvolume diagnostics for a destination endpoint.

    Present only when btrfs snapshots are enabled, the filesystem
    is btrfs, and ``stat`` is available on the host.
    """

    model_config = ConfigDict(frozen=True)

    staging_exists: bool
    staging_is_subvolume: bool
    """Whether ``staging/`` is a btrfs subvolume (inode 256).
    ``False`` when staging does not exist."""
    staging_writable: bool | None = None
    """``None`` when staging does not exist."""


class SnapshotDirsDiagnostics(BaseModel):
    """Snapshot directory diagnostics shared by btrfs and hard-link modes.

    Present only when the endpoint has any snapshot mode enabled.
    """

    model_config = ConfigDict(frozen=True)

    exists: bool
    writable: bool | None = None
    """``None`` when snapshots dir does not exist."""


class SourceEndpointDiagnostics(BaseModel):
    """Observed state of a source sync endpoint.

    Pure diagnostics — no interpretation of what constitutes an error.
    The sync layer translates these into ``SyncError`` values based
    on the sync's configuration and context.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None


class DestinationEndpointDiagnostics(BaseModel):
    """Observed state of a destination sync endpoint.

    Pure diagnostics — no interpretation of what constitutes an error.
    The sync layer translates these into ``SyncError`` values based
    on the sync's configuration and context.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    endpoint_writable: bool
    btrfs: BtrfsStagingSubvolumeDiagnostics | None = None
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None


class SyncStatus(BaseModel):
    """Runtime status of a sync."""

    slug: str
    config: SyncConfig
    source_status: VolumeStatus
    destination_status: VolumeStatus
    source_diagnostics: SourceEndpointDiagnostics | None = None
    destination_diagnostics: DestinationEndpointDiagnostics | None = None
    errors: list[SyncError]
    destination_latest_snapshot: Snapshot | None = None
    """Snapshot from the destination ``latest`` symlink.

    ``None`` when the symlink is absent, invalid, or points to
    ``/dev/null`` (no snapshot yet).
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        sync: SyncConfig,
        src_endpoint: SyncEndpoint,
        dst_endpoint: SyncEndpoint,
        src_status: VolumeStatus,
        dst_status: VolumeStatus,
        src_diag: SourceEndpointDiagnostics | None,
        dst_diag: DestinationEndpointDiagnostics | None,
        all_syncs: dict[str, SyncConfig],
        dry_run: bool,
    ) -> "SyncStatus":
        """Create a ``SyncStatus`` by interpreting diagnostics into errors."""
        if not sync.enabled:
            return SyncStatus(
                slug=sync.slug,
                config=sync,
                source_status=src_status,
                destination_status=dst_status,
                errors=[SyncError.DISABLED],
            )

        src_errors = (
            _source_errors(
                src_diag,  # type: ignore[arg-type]
                src_status.diagnostics.capabilities,  # type: ignore[arg-type]
                src_endpoint,
                sync,
                all_syncs,
                dry_run,
            )
            if src_status.active
            else [SyncError.SRC_VOL_UNAVAILABLE]
        )

        dst_errors = (
            _destination_errors(
                dst_diag,  # type: ignore[arg-type]
                dst_status.diagnostics.capabilities,  # type: ignore[arg-type]
                dst_endpoint,
            )
            if dst_status.active
            else [SyncError.DST_VOL_UNAVAILABLE]
        )

        dst_latest = dst_diag.latest.snapshot if dst_diag and dst_diag.latest else None

        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            source_diagnostics=src_diag,
            destination_diagnostics=dst_diag,
            errors=[*src_errors, *dst_errors],
            destination_latest_snapshot=dst_latest,
        )


# ── Sync diagnostics → errors translation ─────────────────


def _source_errors(
    diag: SourceEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Translate source diagnostics + capabilities into SyncErrors."""
    return [
        *([SyncError.SRC_EP_SENTINEL_NOT_FOUND] if not diag.sentinel_exists else []),
        *_source_rsync_errors(caps),
        *(
            [
                *(
                    [SyncError.SRC_EP_SNAPSHOTS_DIR_NOT_FOUND]
                    if diag.snapshot_dirs is not None and not diag.snapshot_dirs.exists
                    else []
                ),
                *_source_latest_errors(diag, sync, all_syncs, dry_run),
            ]
            if endpoint.snapshot_mode != "none"
            else []
        ),
    ]


def _source_rsync_errors(caps: VolumeCapabilities) -> list[SyncError]:
    """Translate source rsync capability into SyncErrors."""
    match (caps.has_rsync, caps.rsync_version_ok):
        case (False, _):
            return [SyncError.SRC_VOL_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.SRC_VOL_RSYNC_TOO_OLD]
        case _:
            return []


def _source_latest_errors(
    diag: SourceEndpointDiagnostics,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Interpret the source latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncError.SRC_EP_LATEST_SYMLINK_NOT_FOUND]
    elif latest.raw_target == DEVNULL_TARGET:
        # /dev/null interpretation depends on sync-level context
        if not _has_upstream_sync(sync, all_syncs):
            return [SyncError.SRC_EP_LATEST_SYMLINK_INVALID]
        elif dry_run:
            return [SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING]
        else:
            return []
    elif latest.target_valid is False:
        return [SyncError.SRC_EP_LATEST_SYMLINK_INVALID]
    else:
        return []


def _destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncError]:
    """Translate destination diagnostics + capabilities into SyncErrors."""
    return [
        *([SyncError.DST_EP_SENTINEL_NOT_FOUND] if not diag.sentinel_exists else []),
        *_destination_rsync_errors(caps),
        *_destination_snapshot_backend_errors(diag, caps, endpoint),
        *([SyncError.DST_EP_NOT_WRITABLE] if not diag.endpoint_writable else []),
        *(_destination_latest_errors(diag) if endpoint.snapshot_mode != "none" else []),
    ]


def _destination_rsync_errors(caps: VolumeCapabilities) -> list[SyncError]:
    """Translate destination rsync capability into SyncErrors."""
    match (caps.has_rsync, caps.rsync_version_ok):
        case (False, _):
            return [SyncError.DST_VOL_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.DST_VOL_RSYNC_TOO_OLD]
        case _:
            return []


def _destination_snapshot_backend_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncError]:
    """Route to the appropriate snapshot backend error check."""
    if endpoint.btrfs_snapshots.enabled:
        return _btrfs_destination_errors(diag, caps)
    elif endpoint.hard_link_snapshots.enabled:
        return _hardlink_destination_errors(diag, caps)
    else:
        return []


def _btrfs_destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate btrfs-specific diagnostics into SyncErrors."""
    if not caps.has_btrfs:
        return [SyncError.DST_VOL_BTRFS_NOT_FOUND]
    else:
        return [
            *([SyncError.DST_VOL_STAT_NOT_FOUND] if not caps.has_stat else []),
            *([SyncError.DST_VOL_FINDMNT_NOT_FOUND] if not caps.has_findmnt else []),
            *(_btrfs_stat_errors(diag, caps) if caps.has_stat else []),
        ]


def _btrfs_stat_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate btrfs filesystem/subvolume diagnostics (requires stat)."""
    if not caps.is_btrfs_filesystem:
        return [SyncError.DST_VOL_NOT_BTRFS]
    elif diag.btrfs is None:
        return [SyncError.DST_EP_STAGING_NOT_BTRFS_SUBVOLUME]
    else:
        return [
            *(
                [SyncError.DST_VOL_NOT_MOUNTED_USER_SUBVOL_RM]
                if caps.has_findmnt and not caps.btrfs_user_subvol_rm
                else []
            ),
            *_btrfs_staging_errors(diag),
            *(
                [SyncError.DST_EP_STAGING_NOT_BTRFS_SUBVOLUME]
                if diag.btrfs.staging_exists and not diag.btrfs.staging_is_subvolume
                else []
            ),
            *_snapshot_dirs_errors(diag),
        ]


def _btrfs_staging_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Translate btrfs staging directory diagnostics."""
    assert diag.btrfs is not None
    if not diag.btrfs.staging_exists:
        return [SyncError.DST_EP_STAGING_SUBVOL_NOT_FOUND]
    elif diag.btrfs.staging_writable is False:
        return [SyncError.DST_EP_STAGING_SUBVOL_NOT_WRITABLE]
    else:
        return []


def _hardlink_destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate hard-link-specific diagnostics into SyncErrors."""
    if not caps.has_stat:
        return [SyncError.DST_VOL_STAT_NOT_FOUND]
    else:
        return [
            *(
                [SyncError.DST_VOL_NO_HARDLINK_SUPPORT]
                if not caps.hardlink_supported
                else []
            ),
            *_snapshot_dirs_errors(diag),
        ]


def _snapshot_dirs_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Translate snapshot directory diagnostics into SyncErrors."""
    sd = diag.snapshot_dirs
    if sd is None:
        return []
    elif not sd.exists:
        return [SyncError.DST_EP_SNAPSHOTS_DIR_NOT_FOUND]
    elif sd.writable is False:
        return [SyncError.DST_EP_SNAPSHOTS_DIR_NOT_WRITABLE]
    else:
        return []


def _destination_latest_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Interpret the destination latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncError.DST_EP_LATEST_SYMLINK_NOT_FOUND]
    elif latest.target_valid is False:
        return [SyncError.DST_EP_LATEST_SYMLINK_INVALID]
    else:
        return []


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
