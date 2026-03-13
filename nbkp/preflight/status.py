"""Runtime status types for volumes and syncs."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ..config import (
    SyncConfig,
    Volume,
)
from ..conventions import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)


class VolumeReason(str, enum.Enum):
    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"


class SyncReason(str, enum.Enum):
    DISABLED = "disabled"

    SOURCE_UNAVAILABLE = "source unavailable"
    SOURCE_SENTINEL_NOT_FOUND = f"{SOURCE_SENTINEL} source sentinel not found"
    SOURCE_LATEST_NOT_FOUND = f"source {LATEST_LINK} symlink not found"
    SOURCE_LATEST_INVALID = f"source {LATEST_LINK} symlink target is invalid"
    SOURCE_SNAPSHOTS_DIR_NOT_FOUND = f"source {SNAPSHOTS_DIR}/ directory not found"
    SOURCE_RSYNC_NOT_FOUND = "rsync not found on source"
    SOURCE_RSYNC_TOO_OLD = "rsync too old on source (3.0+ required)"

    DESTINATION_UNAVAILABLE = "destination unavailable"
    DESTINATION_SENTINEL_NOT_FOUND = (
        f"{DESTINATION_SENTINEL} destination sentinel not found"
    )
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
    DESTINATION_ENDPOINT_NOT_WRITABLE = "destination endpoint directory not writable"
    DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE = (
        f"destination {SNAPSHOTS_DIR}/ directory not writable"
    )
    DESTINATION_STAGING_DIR_NOT_WRITABLE = (
        f"destination {STAGING_DIR}/ directory not writable"
    )
    DESTINATION_RSYNC_NOT_FOUND = "rsync not found on destination"
    DESTINATION_RSYNC_TOO_OLD = "rsync too old on destination (3.0+ required)"
    DESTINATION_BTRFS_NOT_FOUND = "btrfs not found on destination"
    DESTINATION_STAT_NOT_FOUND = "stat not found on destination"
    DESTINATION_FINDMNT_NOT_FOUND = "findmnt not found on destination"

    DRY_RUN_SOURCE_SNAPSHOT_PENDING = (
        "source snapshot not yet available (dry-run; upstream has not run)"
    )


class VolumeCapabilities(BaseModel):
    """Host- and volume-level capabilities, computed once per active volume."""

    model_config = ConfigDict(frozen=True)

    has_rsync: bool
    rsync_version_ok: bool
    has_btrfs: bool
    has_stat: bool
    has_findmnt: bool
    is_btrfs_filesystem: bool
    hardlink_supported: bool
    btrfs_user_subvol_rm: bool


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

    slug: str
    config: Volume
    reasons: list[VolumeReason]
    capabilities: VolumeCapabilities | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.reasons


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
    snapshot_name: str | None = None
    """Snapshot name extracted from the target path.
    ``None`` when target is ``/dev/null``, invalid, or absent."""


class BtrfsSubvolumeDiagnostics(BaseModel):
    """Btrfs-specific diagnostics for a destination endpoint.

    Present only when btrfs snapshots are enabled, the filesystem
    is btrfs, and ``stat`` is available on the host.
    """

    model_config = ConfigDict(frozen=True)

    is_subvolume: bool
    staging_dir_exists: bool
    staging_dir_writable: bool | None = None
    """``None`` when staging dir does not exist."""


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
    The sync layer translates these into ``SyncReason`` values based
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
    The sync layer translates these into ``SyncReason`` values based
    on the sync's configuration and context.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    endpoint_writable: bool
    btrfs: BtrfsSubvolumeDiagnostics | None = None
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
    reasons: list[SyncReason]
    destination_latest_target: str | None = None
    """Snapshot name from the destination ``latest`` symlink.

    ``None`` when the symlink is absent, invalid, or points to
    ``/dev/null`` (no snapshot yet).  Otherwise, the snapshot
    name only (e.g. ``2026-03-06T14:30:00.000Z`` or
    ``2026-03-06T14-30-00.000Z`` on macOS local volumes).
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.reasons
