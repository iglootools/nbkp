"""Runtime status types for the 4-layer preflight error model.

Capabilities are probed at the level where they physically exist,
but interpreted as errors at the level where the requirement originates.
For example: ``has_btrfs`` is probed at the SSH endpoint level (it's a host
tool), ``is_btrfs_filesystem`` is probed at the volume level (it's a
filesystem property), but both become errors at the sync endpoint level
(because the endpoint config determines whether btrfs is needed).

4-layer error hierarchy:

1. **SSH Endpoint** — Reachability, host tool availability
2. **Volume** — Sentinel, mount config/state, filesystem properties
3. **Sync Endpoint** — Endpoint sentinel, dirs, symlinks, writability,
   capability-gated errors
4. **Sync** — Disabled, ``latest → /dev/null`` interpretation

Each lower layer gates the next: SSH endpoint must be active for volume
checks to run, volume must be active for endpoint checks, etc.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, computed_field

from ..config import (
    MountConfig,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from ..disks.models import (
    MountCapabilities as MountCapabilities,
    MountToolCapabilities as MountToolCapabilities,
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


# ── Layer 1: SSH Endpoint ──────────────────────────────────


class SshEndpointError(str, enum.Enum):
    """Errors at the SSH endpoint (host) level.

    Host tool availability errors live here because tool presence is a
    property of the host, shared by all volumes on that host.
    """

    # Reachability
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"

    # Always needed
    RSYNC_NOT_FOUND = "rsync not found"
    RSYNC_TOO_OLD = "rsync too old (3.0+ required)"

    # Needed when any endpoint on this host has btrfs snapshots
    BTRFS_NOT_FOUND = "btrfs not found"

    # Needed when any endpoint on this host has btrfs or hardlink snapshots
    STAT_NOT_FOUND = "stat not found"

    # Needed when any endpoint on this host has btrfs snapshots
    FINDMNT_NOT_FOUND = "findmnt not found"

    # Needed when any volume on this host has mount config (systemd backend)
    SYSTEMCTL_NOT_FOUND = "systemctl not found"
    SYSTEMD_ESCAPE_NOT_FOUND = "systemd-escape not found"

    # Needed when any volume on this host has mount config (direct backend)
    MOUNT_CMD_NOT_FOUND = "mount command not found"
    UMOUNT_CMD_NOT_FOUND = "umount command not found"
    MOUNTPOINT_CMD_NOT_FOUND = "mountpoint command not found"

    # Needed when any volume on this host has encryption
    SUDO_NOT_FOUND = "sudo not found"
    CRYPTSETUP_NOT_FOUND = "cryptsetup not found"
    SYSTEMD_CRYPTSETUP_NOT_FOUND = "systemd-cryptsetup not found"


@dataclass(frozen=True)
class SshEndpointToolNeeds:
    """What tools are required on this SSH endpoint, derived from config.

    Computed by ``checks.py`` by scanning volumes and endpoints on this host.
    """

    has_btrfs_endpoints: bool = False
    """Any sync endpoint on a volume using this host has btrfs snapshots."""
    has_snapshot_endpoints: bool = False
    """Any sync endpoint on a volume using this host has btrfs or hardlink
    snapshots (stat is needed for both)."""
    mount_systemd: bool = False
    """Any volume on this host uses the systemd mount backend."""
    mount_direct: bool = False
    """Any volume on this host uses the direct mount backend."""
    has_encryption: bool = False
    """Any volume on this host has encryption config."""


class HostToolCapabilities(BaseModel):
    """Host-level tool availability, probed once per SSH endpoint."""

    model_config = ConfigDict(frozen=True)

    has_rsync: bool
    rsync_version_ok: bool
    has_btrfs: bool
    has_stat: bool
    has_findmnt: bool


class SshEndpointDiagnostics(BaseModel):
    """Observed state of an SSH endpoint (or implicit localhost).

    Pure diagnostics — no interpretation of what constitutes an error.
    ``SshEndpointStatus.from_diagnostics`` translates these into
    ``SshEndpointError`` values using ``SshEndpointToolNeeds``.
    """

    model_config = ConfigDict(frozen=True)

    location_excluded: bool = False
    """Whether all SSH endpoints for this volume were excluded by location
    filter.  Only meaningful for remote volumes."""
    ssh_reachable: bool | None = None
    """Whether the SSH endpoint is reachable.
    ``None`` for implicit localhost (always reachable)."""
    host_tools: HostToolCapabilities | None = None
    """Host-level tool availability.
    ``None`` when the host is unreachable or excluded."""
    mount_tools: MountToolCapabilities | None = None
    """Mount management tool availability.
    ``None`` when no volumes on this host have mount config,
    or when the host is unreachable."""


class SshEndpointStatus(BaseModel):
    """Runtime status of an SSH endpoint (or implicit localhost)."""

    slug: str
    """SSH endpoint slug, or ``\"localhost\"`` for local volumes."""
    diagnostics: SshEndpointDiagnostics
    errors: list[SshEndpointError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        diagnostics: SshEndpointDiagnostics,
        needs: SshEndpointToolNeeds = SshEndpointToolNeeds(),
    ) -> SshEndpointStatus:
        """Create status by interpreting diagnostics into errors."""
        return SshEndpointStatus(
            slug=slug,
            diagnostics=diagnostics,
            errors=_ssh_endpoint_errors(diagnostics, needs),
        )


# ── Layer 1 error interpretation ───────────────────────────


def _ssh_endpoint_errors(
    diag: SshEndpointDiagnostics,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """Translate SSH endpoint diagnostics into errors."""
    if diag.location_excluded:
        return [SshEndpointError.LOCATION_EXCLUDED]
    elif diag.ssh_reachable is False:
        return [SshEndpointError.UNREACHABLE]
    elif diag.host_tools is None:
        return []
    else:
        return [
            *_ssh_rsync_errors(diag.host_tools),
            *_ssh_snapshot_tool_errors(diag.host_tools, needs),
            *(
                _ssh_mount_tool_errors(diag.mount_tools, needs)
                if diag.mount_tools is not None
                else []
            ),
        ]


def _ssh_rsync_errors(tools: HostToolCapabilities) -> list[SshEndpointError]:
    """Rsync availability errors (always needed)."""
    match (tools.has_rsync, tools.rsync_version_ok):
        case (False, _):
            return [SshEndpointError.RSYNC_NOT_FOUND]
        case (True, False):
            return [SshEndpointError.RSYNC_TOO_OLD]
        case _:
            return []


def _ssh_snapshot_tool_errors(
    tools: HostToolCapabilities,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """Snapshot-related tool errors (btrfs, stat, findmnt)."""
    return [
        *(
            [SshEndpointError.BTRFS_NOT_FOUND]
            if needs.has_btrfs_endpoints and not tools.has_btrfs
            else []
        ),
        *(
            [SshEndpointError.STAT_NOT_FOUND]
            if needs.has_snapshot_endpoints and not tools.has_stat
            else []
        ),
        *(
            [SshEndpointError.FINDMNT_NOT_FOUND]
            if needs.has_btrfs_endpoints and not tools.has_findmnt
            else []
        ),
    ]


def _ssh_mount_tool_errors(
    mount_tools: MountToolCapabilities,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """Mount management tool errors."""
    return [
        # Systemd tools (systemctl, systemd-escape)
        *(
            [
                *(
                    [SshEndpointError.SYSTEMCTL_NOT_FOUND]
                    if mount_tools.has_systemctl is False
                    else []
                ),
                *(
                    [SshEndpointError.SYSTEMD_ESCAPE_NOT_FOUND]
                    if mount_tools.has_systemd_escape is False
                    else []
                ),
            ]
            if needs.mount_systemd
            else []
        ),
        # Direct tools (mount, umount, mountpoint)
        *(
            [
                *(
                    [SshEndpointError.MOUNT_CMD_NOT_FOUND]
                    if mount_tools.has_mount_cmd is False
                    else []
                ),
                *(
                    [SshEndpointError.UMOUNT_CMD_NOT_FOUND]
                    if mount_tools.has_umount_cmd is False
                    else []
                ),
                *(
                    [SshEndpointError.MOUNTPOINT_CMD_NOT_FOUND]
                    if mount_tools.has_mountpoint is False
                    else []
                ),
            ]
            if needs.mount_direct
            else []
        ),
        # sudo: direct backend always needs it; systemd only for encryption
        *(
            [SshEndpointError.SUDO_NOT_FOUND]
            if mount_tools.has_sudo is False
            and (needs.mount_direct or (needs.has_encryption and needs.mount_systemd))
            else []
        ),
        # cryptsetup: any encryption
        *(
            [SshEndpointError.CRYPTSETUP_NOT_FOUND]
            if mount_tools.has_cryptsetup is False and needs.has_encryption
            else []
        ),
        # systemd-cryptsetup: systemd backend + encryption
        *(
            [SshEndpointError.SYSTEMD_CRYPTSETUP_NOT_FOUND]
            if mount_tools.has_systemd_cryptsetup is False
            and needs.has_encryption
            and needs.mount_systemd
            else []
        ),
    ]


# ── Layer 2: Volume ────────────────────────────────────────


class VolumeError(str, enum.Enum):
    """Volume-level errors.

    Only volume-specific errors remain here.  SSH reachability and host
    tool availability errors moved to ``SshEndpointError``.
    """

    # Mount management — runtime state
    VOLUME_NOT_MOUNTED = "volume not mounted"

    # Sentinel management
    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"

    # Mount management — lifecycle errors
    DEVICE_NOT_PRESENT = "device not plugged in"
    ATTACH_LUKS_FAILED = "failed to attach luks encrypted device"
    MOUNT_FAILED = "failed to mount volume"
    PASSPHRASE_NOT_AVAILABLE = "passphrase not available"

    # Mount management — systemd config validation
    MOUNT_UNIT_NOT_CONFIGURED = "mount unit not configured in systemd"
    MOUNT_UNIT_MISMATCH = "mount unit config does not match nbkp config"
    CRYPTSETUP_SERVICE_NOT_CONFIGURED = "cryptsetup service not configured in systemd"
    CRYPTSETUP_SERVICE_MISMATCH = "cryptsetup service config does not match nbkp config"

    # Auth rules
    POLKIT_RULES_MISSING = "polkit rules not configured"
    SUDOERS_RULES_MISSING = "sudoers rules not configured"

    # Cascade — lower layer inactive
    SSH_ENDPOINT_INACTIVE = "ssh endpoint inactive"


class VolumeCapabilities(BaseModel):
    """Volume-level capabilities, computed once per reachable volume.

    Host-level tool availability (rsync, btrfs, stat, findmnt) lives
    on ``HostToolCapabilities`` at the SSH endpoint level.  This model
    captures only volume-specific filesystem properties.
    """

    model_config = ConfigDict(frozen=True)

    sentinel_exists: bool
    is_btrfs_filesystem: bool
    hardlink_supported: bool
    btrfs_user_subvol_rm: bool
    mount: MountCapabilities | None = None


class VolumeDiagnostics(BaseModel):
    """Observed state of a volume.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``VolumeStatus.from_diagnostics`` translates these into
    ``VolumeError`` values.  SSH reachability and location exclusion
    live on ``SshEndpointDiagnostics``.
    """

    model_config = ConfigDict(frozen=True)

    capabilities: VolumeCapabilities | None = None
    """Volume-level capabilities.
    ``None`` when the volume sentinel is missing and no mount config
    to probe (rare — usually at least sentinel_exists is set)."""


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

    slug: str
    config: Volume
    ssh_endpoint_status: SshEndpointStatus
    diagnostics: VolumeDiagnostics | None
    """``None`` when the SSH endpoint is inactive (unreachable or
    excluded)."""
    errors: list[VolumeError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        config: Volume,
        ssh_endpoint_status: SshEndpointStatus,
        diagnostics: VolumeDiagnostics | None,
    ) -> VolumeStatus:
        """Create status by interpreting diagnostics into errors."""
        if isinstance(config, RemoteVolume) and not ssh_endpoint_status.active:
            return VolumeStatus(
                slug=slug,
                config=config,
                ssh_endpoint_status=ssh_endpoint_status,
                diagnostics=diagnostics,
                errors=[VolumeError.SSH_ENDPOINT_INACTIVE],
            )
        elif diagnostics is None:
            return VolumeStatus(
                slug=slug,
                config=config,
                ssh_endpoint_status=ssh_endpoint_status,
                diagnostics=diagnostics,
                errors=[],
            )
        else:
            mount = getattr(config, "mount", None)
            return VolumeStatus(
                slug=slug,
                config=config,
                ssh_endpoint_status=ssh_endpoint_status,
                diagnostics=diagnostics,
                errors=_volume_errors(diagnostics, mount),
            )


# ── Layer 2 error interpretation ───────────────────────────


def _volume_errors(
    diag: VolumeDiagnostics,
    mount: MountConfig | None = None,
) -> list[VolumeError]:
    """Translate volume diagnostics into VolumeError values."""
    if diag.capabilities is not None and not diag.capabilities.sentinel_exists:
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
    """Translate systemd-specific mount config into VolumeError values.

    Tool availability errors (systemctl, systemd-escape, sudo, etc.)
    are now at the SSH endpoint level.  Only volume-specific config
    validation (mount unit, cryptsetup service, auth rules) remains.
    """
    has_encryption = mount.encryption is not None
    return [
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
    """Translate direct-backend mount config into VolumeError values.

    Tool availability errors (sudo, mount, umount, etc.) are now at the
    SSH endpoint level.  Only volume-specific config validation remains.
    """
    has_encryption = mount.encryption is not None
    return [
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
    """Check if the systemd cryptsetup service config doesn't match."""
    if mount.encryption is None or mount_caps.cryptsetup_service_exec_start is None:
        return False
    exec_start = mount_caps.cryptsetup_service_exec_start
    return (
        mount.encryption.mapper_name not in exec_start
        or mount.device_uuid not in exec_start
    )


# ── Layer 3: Sync Endpoint ─────────────────────────────────


class SourceEndpointError(str, enum.Enum):
    """Errors at the source sync endpoint level."""

    SENTINEL_NOT_FOUND = f"{SOURCE_SENTINEL} sentinel not found"
    SNAPSHOTS_DIR_NOT_FOUND = f"{SNAPSHOTS_DIR}/ directory not found"
    LATEST_SYMLINK_NOT_FOUND = f"{LATEST_LINK} symlink not found"
    LATEST_SYMLINK_INVALID = f"{LATEST_LINK} symlink target is invalid"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume inactive"


class DestinationEndpointError(str, enum.Enum):
    """Errors at the destination sync endpoint level.

    Includes capability-gated errors that are probed at the volume or
    host level but become errors because the endpoint config requires
    a capability the volume doesn't have.  Troubleshoot offers dual
    remediation: fix the volume (e.g. create btrfs filesystem) or
    change the endpoint config (e.g. switch to hard-link snapshots).
    """

    SENTINEL_NOT_FOUND = f"{DESTINATION_SENTINEL} sentinel not found"
    NOT_WRITABLE = "endpoint directory not writable"
    SNAPSHOTS_DIR_NOT_FOUND = f"{SNAPSHOTS_DIR}/ directory not found"
    SNAPSHOTS_DIR_NOT_WRITABLE = f"{SNAPSHOTS_DIR}/ directory not writable"
    LATEST_SYMLINK_NOT_FOUND = f"{LATEST_LINK} symlink not found"
    LATEST_SYMLINK_INVALID = f"{LATEST_LINK} symlink target is invalid"
    STAGING_SUBVOL_NOT_FOUND = f"{STAGING_DIR}/ directory not found"
    STAGING_NOT_BTRFS_SUBVOLUME = f"{STAGING_DIR}/ is not a btrfs subvolume"
    STAGING_SUBVOL_NOT_WRITABLE = f"{STAGING_DIR}/ subvolume not writable"

    # Capability-gated: probed at volume level, error because endpoint config
    # requires the capability.
    VOL_NOT_BTRFS = "volume not on btrfs filesystem"
    VOL_NOT_MOUNTED_USER_SUBVOL_RM = "volume not mounted with user_subvol_rm_allowed"
    VOL_NO_HARDLINK_SUPPORT = "volume filesystem does not support hard links"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume inactive"


# Diagnostics models (shared by source and destination endpoints)


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
    ``SourceEndpointStatus.from_diagnostics`` translates these into
    ``SourceEndpointError`` values.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None


class DestinationEndpointDiagnostics(BaseModel):
    """Observed state of a destination sync endpoint.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``DestinationEndpointStatus.from_diagnostics`` translates these into
    ``DestinationEndpointError`` values.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    endpoint_writable: bool
    btrfs: BtrfsStagingSubvolumeDiagnostics | None = None
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None


# Sync endpoint status models


class SourceEndpointStatus(BaseModel):
    """Runtime status of a source sync endpoint."""

    endpoint_slug: str
    volume_status: VolumeStatus
    diagnostics: SourceEndpointDiagnostics | None
    """``None`` when the volume is inactive."""
    errors: list[SourceEndpointError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        endpoint: SyncEndpoint,
        volume_status: VolumeStatus,
        diagnostics: SourceEndpointDiagnostics | None,
    ) -> SourceEndpointStatus:
        """Create status by interpreting diagnostics into errors."""
        if not volume_status.active:
            errors = [SourceEndpointError.VOLUME_INACTIVE]
        elif diagnostics is not None:
            errors = _source_endpoint_errors(diagnostics, endpoint)
        else:
            errors = []
        return SourceEndpointStatus(
            endpoint_slug=endpoint.slug,
            volume_status=volume_status,
            diagnostics=diagnostics,
            errors=errors,
        )


class DestinationEndpointStatus(BaseModel):
    """Runtime status of a destination sync endpoint."""

    endpoint_slug: str
    volume_status: VolumeStatus
    diagnostics: DestinationEndpointDiagnostics | None
    """``None`` when the volume is inactive."""
    errors: list[DestinationEndpointError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        endpoint: SyncEndpoint,
        volume_status: VolumeStatus,
        diagnostics: DestinationEndpointDiagnostics | None,
    ) -> DestinationEndpointStatus:
        """Create status by interpreting diagnostics into errors."""
        if not volume_status.active:
            errors: list[DestinationEndpointError] = [
                DestinationEndpointError.VOLUME_INACTIVE
            ]
        elif diagnostics is not None:
            # Capability-gated errors need host tools and volume capabilities
            host_tools = volume_status.ssh_endpoint_status.diagnostics.host_tools
            vol_caps = (
                volume_status.diagnostics.capabilities
                if volume_status.diagnostics is not None
                else None
            )
            errors = _destination_endpoint_errors(
                diagnostics, vol_caps, host_tools, endpoint
            )
        else:
            errors = []
        return DestinationEndpointStatus(
            endpoint_slug=endpoint.slug,
            volume_status=volume_status,
            diagnostics=diagnostics,
            errors=errors,
        )


# ── Layer 3 error interpretation ───────────────────────────


def _source_endpoint_errors(
    diag: SourceEndpointDiagnostics,
    endpoint: SyncEndpoint,
) -> list[SourceEndpointError]:
    """Translate source endpoint diagnostics into errors."""
    return [
        *([SourceEndpointError.SENTINEL_NOT_FOUND] if not diag.sentinel_exists else []),
        *(
            [
                *(
                    [SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND]
                    if diag.snapshot_dirs is not None and not diag.snapshot_dirs.exists
                    else []
                ),
                *_source_latest_ep_errors(diag),
            ]
            if endpoint.snapshot_mode != "none"
            else []
        ),
    ]


def _source_latest_ep_errors(
    diag: SourceEndpointDiagnostics,
) -> list[SourceEndpointError]:
    """Interpret source latest symlink state at endpoint level.

    Only structural validity is checked here.  The ``/dev/null``
    interpretation requires sync-level context (upstream sync check)
    and stays at Layer 4.
    """
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SourceEndpointError.LATEST_SYMLINK_NOT_FOUND]
    elif latest.raw_target == DEVNULL_TARGET:
        # /dev/null is structurally valid — interpretation deferred to sync
        return []
    elif latest.target_valid is False:
        return [SourceEndpointError.LATEST_SYMLINK_INVALID]
    else:
        return []


def _destination_endpoint_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
    endpoint: SyncEndpoint,
) -> list[DestinationEndpointError]:
    """Translate destination endpoint diagnostics into errors."""
    return [
        *(
            [DestinationEndpointError.SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *_destination_snapshot_backend_ep_errors(diag, caps, host_tools, endpoint),
        *(
            [DestinationEndpointError.NOT_WRITABLE]
            if not diag.endpoint_writable
            else []
        ),
        *(
            _destination_latest_ep_errors(diag)
            if endpoint.snapshot_mode != "none"
            else []
        ),
    ]


def _destination_snapshot_backend_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
    endpoint: SyncEndpoint,
) -> list[DestinationEndpointError]:
    """Route to the appropriate snapshot backend error check."""
    if endpoint.btrfs_snapshots.enabled:
        return _btrfs_destination_ep_errors(diag, caps, host_tools)
    elif endpoint.hard_link_snapshots.enabled:
        return _hardlink_destination_ep_errors(diag, caps, host_tools)
    else:
        return []


def _btrfs_destination_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
) -> list[DestinationEndpointError]:
    """Btrfs destination endpoint errors.

    Tool availability (btrfs, stat, findmnt) errors are reported at the
    SSH endpoint level.  Here we check capability-gated errors: the
    endpoint config requires btrfs but the volume may not support it.
    """
    if caps is None or host_tools is None:
        return []
    if not host_tools.has_stat:
        # Can't determine filesystem type — stat error at SSH endpoint level
        return []
    elif not caps.is_btrfs_filesystem:
        return [DestinationEndpointError.VOL_NOT_BTRFS]
    else:
        return [
            *(
                [DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM]
                if host_tools.has_findmnt and not caps.btrfs_user_subvol_rm
                else []
            ),
            *_btrfs_staging_ep_errors(diag),
            *(
                [DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME]
                if diag.btrfs is not None
                and diag.btrfs.staging_exists
                and not diag.btrfs.staging_is_subvolume
                else []
            ),
            *_snapshot_dirs_ep_errors(diag),
        ]


def _btrfs_staging_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointError]:
    """Translate btrfs staging directory diagnostics."""
    if diag.btrfs is None:
        return []
    if not diag.btrfs.staging_exists:
        return [DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND]
    elif diag.btrfs.staging_writable is False:
        return [DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE]
    else:
        return []


def _hardlink_destination_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
) -> list[DestinationEndpointError]:
    """Hard-link destination endpoint errors."""
    if caps is None or host_tools is None:
        return []
    if not host_tools.has_stat:
        # stat error handled at SSH endpoint level
        return []
    else:
        return [
            *(
                [DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT]
                if not caps.hardlink_supported
                else []
            ),
            *_snapshot_dirs_ep_errors(diag),
        ]


def _snapshot_dirs_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointError]:
    """Translate snapshot directory diagnostics into errors."""
    sd = diag.snapshot_dirs
    if sd is None:
        return []
    elif not sd.exists:
        return [DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND]
    elif sd.writable is False:
        return [DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE]
    else:
        return []


def _destination_latest_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointError]:
    """Interpret the destination latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND]
    elif latest.target_valid is False:
        return [DestinationEndpointError.LATEST_SYMLINK_INVALID]
    else:
        return []


# ── Layer 4: Sync ──────────────────────────────────────────


class SyncError(str, enum.Enum):
    """Sync-level errors.

    Drastically reduced from the pre-refactor version.  Most errors
    that were previously ``SyncError`` variants now live at the SSH
    endpoint, volume, or sync endpoint level.  Only errors that
    require sync-graph context remain here.
    """

    DISABLED = "disabled"
    SRC_EP_LATEST_DEVNULL_NO_UPSTREAM = (
        "source latest \u2192 /dev/null with no upstream sync"
    )
    DRY_RUN_SRC_EP_SNAPSHOT_PENDING = (
        "source snapshot not yet available (dry-run; upstream has not run)"
    )

    # Cascade — lower layer inactive
    SOURCE_ENDPOINT_INACTIVE = "source endpoint inactive"
    DESTINATION_ENDPOINT_INACTIVE = "destination endpoint inactive"


class SyncStatus(BaseModel):
    """Runtime status of a sync."""

    slug: str
    config: SyncConfig
    source_endpoint_status: SourceEndpointStatus
    destination_endpoint_status: DestinationEndpointStatus
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

    def is_expected_inactive(self) -> bool:
        """Whether all errors across all 4 layers are expected-inactive.

        Used to distinguish syncs that are inactive due to expected
        conditions (missing sentinels, offline volumes) from those with
        real infrastructure errors.
        """
        if self.active:
            return False

        src_ep = self.source_endpoint_status
        dst_ep = self.destination_endpoint_status
        return all(
            [
                (
                    set(src_ep.volume_status.ssh_endpoint_status.errors)
                    <= INACTIVE_SSH_ERRORS
                    if src_ep.volume_status.ssh_endpoint_status.errors
                    else True
                ),
                (
                    set(dst_ep.volume_status.ssh_endpoint_status.errors)
                    <= INACTIVE_SSH_ERRORS
                    if dst_ep.volume_status.ssh_endpoint_status.errors
                    else True
                ),
                (
                    set(src_ep.volume_status.errors) <= INACTIVE_VOLUME_ERRORS
                    if src_ep.volume_status.errors
                    else True
                ),
                (
                    set(dst_ep.volume_status.errors) <= INACTIVE_VOLUME_ERRORS
                    if dst_ep.volume_status.errors
                    else True
                ),
                (
                    set(src_ep.errors) <= INACTIVE_SRC_ENDPOINT_ERRORS
                    if src_ep.errors
                    else True
                ),
                (
                    set(dst_ep.errors) <= INACTIVE_DST_ENDPOINT_ERRORS
                    if dst_ep.errors
                    else True
                ),
                (set(self.errors) <= INACTIVE_SYNC_ERRORS if self.errors else True),
            ]
        )

    @staticmethod
    def from_diagnostics(
        sync: SyncConfig,
        src_endpoint: SyncEndpoint,
        src_ep_status: SourceEndpointStatus,
        dst_ep_status: DestinationEndpointStatus,
        all_syncs: dict[str, SyncConfig],
        dry_run: bool,
    ) -> SyncStatus:
        """Create status by interpreting sync-level errors."""
        if not sync.enabled:
            errors: list[SyncError] = [SyncError.DISABLED]
        else:
            errors = [
                *_sync_errors(sync, src_endpoint, src_ep_status, all_syncs, dry_run),
                *(
                    [SyncError.SOURCE_ENDPOINT_INACTIVE]
                    if not src_ep_status.active
                    else []
                ),
                *(
                    [SyncError.DESTINATION_ENDPOINT_INACTIVE]
                    if not dst_ep_status.active
                    else []
                ),
            ]

        dst_diag = dst_ep_status.diagnostics
        dst_latest = dst_diag.latest.snapshot if dst_diag and dst_diag.latest else None

        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_endpoint_status=src_ep_status,
            destination_endpoint_status=dst_ep_status,
            errors=errors,
            destination_latest_snapshot=dst_latest,
        )


# ── Layer 4 error interpretation ───────────────────────────


def _sync_errors(
    sync: SyncConfig,
    src_endpoint: SyncEndpoint,
    src_ep_status: SourceEndpointStatus,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Sync-level errors: ``latest → /dev/null`` interpretation.

    Only errors that require sync-graph context (upstream dependency)
    remain at this level.  All other errors cascade from lower layers.
    """
    if not src_ep_status.active:
        return []
    if src_endpoint.snapshot_mode == "none":
        return []
    diag = src_ep_status.diagnostics
    if diag is None or diag.latest is None:
        return []
    if diag.latest.raw_target != DEVNULL_TARGET:
        return []
    # latest → /dev/null: interpret based on sync graph
    if not _has_upstream_sync(sync, all_syncs):
        return [SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM]
    elif dry_run:
        return [SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING]
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


# ── Inactive error sets ────────────────────────────────────

INACTIVE_SSH_ERRORS: frozenset[SshEndpointError] = frozenset(
    {
        SshEndpointError.UNREACHABLE,
        SshEndpointError.LOCATION_EXCLUDED,
    }
)

INACTIVE_VOLUME_ERRORS: frozenset[VolumeError] = frozenset(
    {
        VolumeError.SENTINEL_NOT_FOUND,
        VolumeError.DEVICE_NOT_PRESENT,
        VolumeError.VOLUME_NOT_MOUNTED,
        VolumeError.SSH_ENDPOINT_INACTIVE,
    }
)

INACTIVE_SRC_ENDPOINT_ERRORS: frozenset[SourceEndpointError] = frozenset(
    {
        SourceEndpointError.SENTINEL_NOT_FOUND,
        SourceEndpointError.VOLUME_INACTIVE,
    }
)

INACTIVE_DST_ENDPOINT_ERRORS: frozenset[DestinationEndpointError] = frozenset(
    {
        DestinationEndpointError.SENTINEL_NOT_FOUND,
        DestinationEndpointError.VOLUME_INACTIVE,
    }
)

INACTIVE_SYNC_ERRORS: frozenset[SyncError] = frozenset(
    {
        SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING,
        SyncError.SOURCE_ENDPOINT_INACTIVE,
        SyncError.DESTINATION_ENDPOINT_INACTIVE,
    }
)


# ── Preflight result ───────────────────────────────────────


@dataclass(frozen=True)
class PreflightResult:
    """Complete result of the 4-phase preflight check cascade."""

    ssh_endpoint_statuses: dict[str, SshEndpointStatus]
    volume_statuses: dict[str, VolumeStatus]
    source_endpoint_statuses: dict[str, SourceEndpointStatus]
    destination_endpoint_statuses: dict[str, DestinationEndpointStatus]
    sync_statuses: dict[str, SyncStatus]
