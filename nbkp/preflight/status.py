"""Runtime status types for volumes and syncs."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ..config import (
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
    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"


class SyncError(str, enum.Enum):
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
        return VolumeStatus(
            slug=slug,
            config=config,
            diagnostics=diagnostics,
            errors=_volume_errors(diagnostics),
        )


def _volume_errors(diag: VolumeDiagnostics) -> list[VolumeError]:
    """Translate volume diagnostics into VolumeError values."""
    if diag.location_excluded:
        return [VolumeError.LOCATION_EXCLUDED]
    elif diag.ssh_reachable is False:
        return [VolumeError.UNREACHABLE]
    elif diag.capabilities is not None and not diag.capabilities.sentinel_exists:
        return [VolumeError.SENTINEL_NOT_FOUND]
    else:
        return []


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
            else [SyncError.SOURCE_UNAVAILABLE]
        )

        dst_errors = (
            _destination_errors(
                dst_diag,  # type: ignore[arg-type]
                dst_status.diagnostics.capabilities,  # type: ignore[arg-type]
                dst_endpoint,
            )
            if dst_status.active
            else [SyncError.DESTINATION_UNAVAILABLE]
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
        *([SyncError.SOURCE_SENTINEL_NOT_FOUND] if not diag.sentinel_exists else []),
        *_source_rsync_errors(caps),
        *(
            [
                *(
                    [SyncError.SOURCE_SNAPSHOTS_DIR_NOT_FOUND]
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
            return [SyncError.SOURCE_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.SOURCE_RSYNC_TOO_OLD]
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
        return [SyncError.SOURCE_LATEST_NOT_FOUND]
    elif latest.raw_target == DEVNULL_TARGET:
        # /dev/null interpretation depends on sync-level context
        if not _has_upstream_sync(sync, all_syncs):
            return [SyncError.SOURCE_LATEST_INVALID]
        elif dry_run:
            return [SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING]
        else:
            return []
    elif latest.target_valid is False:
        return [SyncError.SOURCE_LATEST_INVALID]
    else:
        return []


def _destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncError]:
    """Translate destination diagnostics + capabilities into SyncErrors."""
    return [
        *(
            [SyncError.DESTINATION_SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *_destination_rsync_errors(caps),
        *_destination_snapshot_backend_errors(diag, caps, endpoint),
        *(
            [SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE]
            if not diag.endpoint_writable
            else []
        ),
        *(_destination_latest_errors(diag) if endpoint.snapshot_mode != "none" else []),
    ]


def _destination_rsync_errors(caps: VolumeCapabilities) -> list[SyncError]:
    """Translate destination rsync capability into SyncErrors."""
    match (caps.has_rsync, caps.rsync_version_ok):
        case (False, _):
            return [SyncError.DESTINATION_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.DESTINATION_RSYNC_TOO_OLD]
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
        return [SyncError.DESTINATION_BTRFS_NOT_FOUND]
    else:
        return [
            *([SyncError.DESTINATION_STAT_NOT_FOUND] if not caps.has_stat else []),
            *(
                [SyncError.DESTINATION_FINDMNT_NOT_FOUND]
                if not caps.has_findmnt
                else []
            ),
            *(_btrfs_stat_errors(diag, caps) if caps.has_stat else []),
        ]


def _btrfs_stat_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate btrfs filesystem/subvolume diagnostics (requires stat)."""
    if not caps.is_btrfs_filesystem:
        return [SyncError.DESTINATION_NOT_BTRFS]
    elif diag.btrfs is None or not diag.btrfs.is_subvolume:
        return [SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME]
    else:
        return [
            *(
                [SyncError.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM]
                if caps.has_findmnt and not caps.btrfs_user_subvol_rm
                else []
            ),
            *_btrfs_staging_errors(diag),
            *_snapshot_dirs_errors(diag),
        ]


def _btrfs_staging_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Translate btrfs staging directory diagnostics."""
    assert diag.btrfs is not None
    if not diag.btrfs.staging_dir_exists:
        return [SyncError.DESTINATION_TMP_NOT_FOUND]
    elif diag.btrfs.staging_dir_writable is False:
        return [SyncError.DESTINATION_STAGING_DIR_NOT_WRITABLE]
    else:
        return []


def _hardlink_destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate hard-link-specific diagnostics into SyncErrors."""
    if not caps.has_stat:
        return [SyncError.DESTINATION_STAT_NOT_FOUND]
    else:
        return [
            *(
                [SyncError.DESTINATION_NO_HARDLINK_SUPPORT]
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
        return [SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND]
    elif sd.writable is False:
        return [SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE]
    else:
        return []


def _destination_latest_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Interpret the destination latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncError.DESTINATION_LATEST_NOT_FOUND]
    elif latest.target_valid is False:
        return [SyncError.DESTINATION_LATEST_INVALID]
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
