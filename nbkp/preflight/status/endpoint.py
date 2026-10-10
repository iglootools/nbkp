"""Layer 3 — Sync endpoints (source and destination).

Endpoint sentinel, directories, symlinks, writability, and the
capability-gated errors whose requirement comes from the endpoint config.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ...config import (
    SyncEndpoint,
)
from ...fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    Snapshot,
)
from .ssh import HostToolCapabilities
from .volume import VolumeCapabilities, VolumeStatus


class SourceEndpointError(str, enum.Enum):
    """Errors at the source sync endpoint level."""

    SENTINEL_NOT_FOUND = f"{SOURCE_SENTINEL} sentinel not found"
    SNAPSHOTS_DIR_NOT_FOUND = f"{SNAPSHOTS_DIR}/ directory not found"
    LATEST_SYMLINK_NOT_FOUND = f"{LATEST_LINK} symlink not found"
    LATEST_SYMLINK_INVALID = f"{LATEST_LINK} symlink target is invalid"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume inactive"


INACTIVE_SRC_ENDPOINT_ERRORS: frozenset[SourceEndpointError] = frozenset(
    {
        SourceEndpointError.SENTINEL_NOT_FOUND,
        SourceEndpointError.VOLUME_INACTIVE,
    }
)


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
    VOL_FS_TYPE_UNKNOWN = "could not determine the volume filesystem type"
    VOL_NOT_MOUNTED_USER_SUBVOL_RM = "volume not mounted with user_subvol_rm_allowed"
    VOL_NO_HARDLINK_SUPPORT = "volume filesystem does not support hard links"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume inactive"


INACTIVE_DST_ENDPOINT_ERRORS: frozenset[DestinationEndpointError] = frozenset(
    {
        DestinationEndpointError.SENTINEL_NOT_FOUND,
        DestinationEndpointError.VOLUME_INACTIVE,
    }
)


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

    model_config = ConfigDict(frozen=True)

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
        return SourceEndpointStatus(
            endpoint_slug=endpoint.slug,
            volume_status=volume_status,
            diagnostics=diagnostics,
            errors=(
                [SourceEndpointError.VOLUME_INACTIVE]
                if not volume_status.active
                else _source_endpoint_errors(diagnostics, endpoint)
                if diagnostics is not None
                else []
            ),
        )


class DestinationEndpointStatus(BaseModel):
    """Runtime status of a destination sync endpoint."""

    model_config = ConfigDict(frozen=True)

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
        """Create status by interpreting diagnostics into errors.

        Capability-gated errors need the host tools and volume capabilities
        probed at the lower layers.
        """
        vol_caps = (
            volume_status.diagnostics.capabilities
            if volume_status.diagnostics is not None
            else None
        )
        return DestinationEndpointStatus(
            endpoint_slug=endpoint.slug,
            volume_status=volume_status,
            diagnostics=diagnostics,
            errors=(
                [DestinationEndpointError.VOLUME_INACTIVE]
                if not volume_status.active
                else _destination_endpoint_errors(
                    diagnostics,
                    vol_caps,
                    volume_status.ssh_endpoint_status.diagnostics.host_tools,
                    endpoint,
                )
                if diagnostics is not None
                else []
            ),
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
    """Translate destination endpoint diagnostics into errors.

    ``NOT_WRITABLE`` is only emitted when the endpoint directory is
    known to exist — approximated via ``sentinel_exists`` (the sentinel
    lives inside the endpoint dir, so its presence proves the dir
    exists).  When the sentinel is missing we suppress NOT_WRITABLE
    because the ``test -w`` probe also fails on non-existent paths, and
    the SENTINEL_NOT_FOUND fix already covers the "create the dir"
    case.
    """
    return [
        *(
            [DestinationEndpointError.SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *_destination_snapshot_backend_ep_errors(diag, caps, host_tools, endpoint),
        *(
            [DestinationEndpointError.NOT_WRITABLE]
            if not diag.endpoint_writable and diag.sentinel_exists
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
    match (caps, host_tools):
        case (None, _) | (_, None):
            return []
        case (_, HostToolCapabilities(has_stat=False)):
            # Can't determine filesystem type — stat error at SSH endpoint level
            return []
        case (VolumeCapabilities(is_btrfs_filesystem=None), _):
            return [DestinationEndpointError.VOL_FS_TYPE_UNKNOWN]
        case (VolumeCapabilities(is_btrfs_filesystem=False), _):
            return [DestinationEndpointError.VOL_NOT_BTRFS]
        case (VolumeCapabilities() as vol_caps, HostToolCapabilities() as tools):
            return _btrfs_volume_ep_errors(diag, vol_caps, tools)
        case _:
            return []


def _btrfs_volume_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    host_tools: HostToolCapabilities,
) -> list[DestinationEndpointError]:
    """Btrfs endpoint errors once the volume is known to be on btrfs."""
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
