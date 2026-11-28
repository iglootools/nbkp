"""
Layer 3 — Sync endpoints (source and destination).

Endpoint sentinel, directories, symlinks, writability, and the
capability-gated errors whose requirement comes from the endpoint config.
"""

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from nbkp.config import SyncEndpoint
from nbkp.fsprotocol import DEVNULL_TARGET, Snapshot
from nbkp.preflight.status.ssh import HostToolCapabilities
from nbkp.preflight.status.volume import VolumeCapabilities, VolumeStatus


class SourceEndpointErrorKind(enum.StrEnum):
    """Why a source endpoint cannot be read from."""

    SENTINEL_NOT_FOUND = "sentinel-not-found"
    # The sentinel may exist behind a directory nbkp cannot search.
    SENTINEL_UNREADABLE = "sentinel-unreadable"
    SNAPSHOTS_DIR_NOT_FOUND = "snapshots-dir-not-found"
    LATEST_SYMLINK_NOT_FOUND = "latest-symlink-not-found"
    LATEST_SYMLINK_INVALID = "latest-symlink-invalid"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume-inactive"


INACTIVE_SRC_ENDPOINT_ERRORS: frozenset[SourceEndpointErrorKind] = frozenset(
    {
        SourceEndpointErrorKind.SENTINEL_NOT_FOUND,
        SourceEndpointErrorKind.VOLUME_INACTIVE,
    },
)


class DestinationEndpointErrorKind(enum.StrEnum):
    """
    Errors of a destination endpoint, capability-gated ones included.

    Capability-gated errors are probed at the volume or host level but become errors
    because the endpoint config requires a capability the volume doesn't have.
    Troubleshoot offers dual remediation: fix the volume (e.g. create btrfs filesystem)
    or change the endpoint config (e.g. switch to hard-link snapshots).
    """

    SENTINEL_NOT_FOUND = "sentinel-not-found"
    # The sentinel may exist behind a directory nbkp cannot search.
    SENTINEL_UNREADABLE = "sentinel-unreadable"
    NOT_WRITABLE = "not-writable"
    SNAPSHOTS_DIR_NOT_FOUND = "snapshots-dir-not-found"
    SNAPSHOTS_DIR_NOT_WRITABLE = "snapshots-dir-not-writable"
    LATEST_SYMLINK_NOT_FOUND = "latest-symlink-not-found"
    LATEST_SYMLINK_INVALID = "latest-symlink-invalid"
    STAGING_SUBVOL_NOT_FOUND = "staging-subvol-not-found"
    STAGING_NOT_BTRFS_SUBVOLUME = "staging-not-btrfs-subvolume"
    STAGING_SUBVOL_NOT_WRITABLE = "staging-subvol-not-writable"

    # Capability-gated: probed at volume level, error because endpoint config
    # requires the capability.
    VOL_NOT_BTRFS = "vol-not-btrfs"
    VOL_FS_TYPE_UNKNOWN = "vol-fs-type-unknown"
    VOL_NOT_MOUNTED_USER_SUBVOL_RM = "vol-not-mounted-user-subvol-rm"
    VOL_NO_HARDLINK_SUPPORT = "vol-no-hardlink-support"

    # Cascade — lower layer inactive
    VOLUME_INACTIVE = "volume-inactive"


INACTIVE_DST_ENDPOINT_ERRORS: frozenset[DestinationEndpointErrorKind] = frozenset(
    {
        DestinationEndpointErrorKind.SENTINEL_NOT_FOUND,
        DestinationEndpointErrorKind.VOLUME_INACTIVE,
    },
)


class DestinationEndpointWarningKind(enum.StrEnum):
    """
    Endpoint findings worth fixing that do not make the endpoint inactive.

    Kept apart from ``DestinationEndpointErrorKind`` so they never reach
    ``active`` or the strictness policy: ``check`` and ``troubleshoot``
    display them, and the sync still runs.
    """

    # The snapshot ``latest`` points to has lost its btrfs read-only flag
    # (a delete that failed after clearing it, or a manual change), so it
    # can be modified.
    LATEST_SNAPSHOT_WRITABLE = "latest-snapshot-writable"


class LatestSymlinkState(BaseModel):
    """
    The raw ``latest`` readlink result, and whether its target directory exists.

    It does not interpret what constitutes an error.
    """

    model_config = ConfigDict(frozen=True)

    exists: bool
    raw_target: str | None = None
    """Raw readlink value.  ``/dev/null``, a relative path, or ``None``
    when the symlink is absent or unreadable."""
    target_valid: bool | None = None
    """Whether the target is an existing directory named like a snapshot.
    ``None`` when there is no symlink or target is ``/dev/null``."""
    snapshot: Snapshot | None = None
    """``None`` when target is ``/dev/null``, invalid, or absent."""


class BtrfsStagingSubvolumeDiagnostics(BaseModel):
    """
    The state of the btrfs ``staging`` subvolume.

    Present only when btrfs snapshots are enabled, the filesystem is btrfs, and ``stat``
    is available on the host.
    """

    model_config = ConfigDict(frozen=True)

    staging_exists: bool
    staging_is_subvolume: bool
    """Whether ``staging/`` is a btrfs subvolume (inode 256).
    ``False`` when staging does not exist."""
    staging_writable: bool | None = None
    """``None`` when staging does not exist."""


class SnapshotDirsDiagnostics(BaseModel):
    """
    Snapshot directory diagnostics shared by btrfs and hard-link modes.

    Present only when the endpoint has any snapshot mode enabled.
    """

    model_config = ConfigDict(frozen=True)

    exists: bool
    writable: bool | None = None
    """``None`` when snapshots dir does not exist."""


class SourceEndpointDiagnostics(BaseModel):
    """
    Observed state of a source sync endpoint.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``SourceEndpointStatus.from_diagnostics`` translates these into
    ``SourceEndpointErrorKind`` values.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    sentinel_unreadable: str | None = None
    """Why the probe could not tell whether the sentinel exists (a directory on
    its path is not searchable), or ``None`` when it could. When set, nothing
    else was probed: every other field keeps its default."""
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None


class DestinationEndpointDiagnostics(BaseModel):
    """
    Observed state of a destination sync endpoint.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``DestinationEndpointStatus.from_diagnostics`` translates these into
    ``DestinationEndpointErrorKind`` values.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    sentinel_exists: bool
    sentinel_unreadable: str | None = None
    """Why the probe could not tell whether the sentinel exists (a directory on
    its path is not searchable), or ``None`` when it could. When set, nothing
    else was probed: every other field keeps its default."""
    endpoint_writable: bool
    btrfs: BtrfsStagingSubvolumeDiagnostics | None = None
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None
    latest_snapshot_readonly: bool | None = None
    """Whether the btrfs snapshot ``latest`` points to is read-only.  ``None``
    when not probed (no btrfs snapshots, no valid ``latest``, no ``btrfs``
    tool, not on btrfs) or when the probe failed."""


class SourceEndpointStatus(BaseModel):
    """A source endpoint's diagnostics and the errors they mean."""

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    volume_status: VolumeStatus
    diagnostics: SourceEndpointDiagnostics | None
    """``None`` when the volume is inactive."""
    errors: list[SourceEndpointErrorKind]

    @computed_field
    @property
    def active(self) -> bool:
        """Return whether the source endpoint has no errors."""
        return not self.errors

    @staticmethod
    def from_diagnostics(
        endpoint: SyncEndpoint,
        volume_status: VolumeStatus,
        diagnostics: SourceEndpointDiagnostics | None,
    ) -> SourceEndpointStatus:
        """
        Build the status from the diagnostics and the volume's status.

        An inactive volume gives only ``VOLUME_INACTIVE``; the snapshot errors apply
        only when the endpoint has snapshots.
        """
        return SourceEndpointStatus(
            endpoint_slug=endpoint.slug,
            volume_status=volume_status,
            diagnostics=diagnostics,
            errors=(
                [SourceEndpointErrorKind.VOLUME_INACTIVE]
                if not volume_status.active
                else _source_endpoint_errors(diagnostics, endpoint)
                if diagnostics is not None
                else []
            ),
        )


class DestinationEndpointStatus(BaseModel):
    """A destination endpoint's diagnostics, and the errors and warnings they mean."""

    model_config = ConfigDict(frozen=True)

    endpoint_slug: str
    volume_status: VolumeStatus
    diagnostics: DestinationEndpointDiagnostics | None
    """``None`` when the volume is inactive."""
    errors: list[DestinationEndpointErrorKind]
    warnings: list[DestinationEndpointWarningKind] = []
    """Non-fatal findings; never affect ``active``."""

    @computed_field
    @property
    def active(self) -> bool:
        """Return whether the destination endpoint has no errors."""
        return not self.errors

    @staticmethod
    def from_diagnostics(
        endpoint: SyncEndpoint,
        volume_status: VolumeStatus,
        diagnostics: DestinationEndpointDiagnostics | None,
    ) -> DestinationEndpointStatus:
        """
        Build the status from the diagnostics and the layers below.

        Capability-gated errors need the host tools and volume capabilities probed at
        the lower layers.
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
                [DestinationEndpointErrorKind.VOLUME_INACTIVE]
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
            warnings=(
                [DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE]
                if volume_status.active
                and diagnostics is not None
                and diagnostics.latest_snapshot_readonly is False
                else []
            ),
        )


def _source_endpoint_errors(
    diag: SourceEndpointDiagnostics,
    endpoint: SyncEndpoint,
) -> list[SourceEndpointErrorKind]:
    return (
        [SourceEndpointErrorKind.SENTINEL_UNREADABLE]
        if diag.sentinel_unreadable is not None
        else _observed_source_endpoint_errors(diag, endpoint)
    )


def _observed_source_endpoint_errors(
    diag: SourceEndpointDiagnostics,
    endpoint: SyncEndpoint,
) -> list[SourceEndpointErrorKind]:
    return [
        *(
            [SourceEndpointErrorKind.SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *(
            [
                *(
                    [SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND]
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
) -> list[SourceEndpointErrorKind]:
    """
    Return the structural errors of a source endpoint's ``latest``.

    Only structural validity is checked here. The ``/dev/null`` interpretation requires
    sync-level context (upstream sync check) and stays at Layer 4.
    """
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND]
    elif latest.raw_target == DEVNULL_TARGET:
        # /dev/null is structurally valid — interpretation deferred to sync
        return []
    elif latest.target_valid is False:
        return [SourceEndpointErrorKind.LATEST_SYMLINK_INVALID]
    else:
        return []


def _destination_endpoint_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
    endpoint: SyncEndpoint,
) -> list[DestinationEndpointErrorKind]:
    """
    Return a destination endpoint's errors.

    ``NOT_WRITABLE`` is only emitted when the endpoint directory is known to exist,
    approximated via ``sentinel_exists`` (the sentinel lives inside the endpoint dir, so
    its presence proves the dir exists). When the sentinel is missing we suppress
    NOT_WRITABLE because the ``test -w`` probe also fails on non-existent paths, and the
    SENTINEL_NOT_FOUND fix already covers the "create the dir" case. A sentinel
    nobody can tell the presence of gives ``SENTINEL_UNREADABLE`` alone, since
    nothing else was probed.
    """
    return (
        [DestinationEndpointErrorKind.SENTINEL_UNREADABLE]
        if diag.sentinel_unreadable is not None
        else _observed_destination_endpoint_errors(diag, caps, host_tools, endpoint)
    )


def _observed_destination_endpoint_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
    endpoint: SyncEndpoint,
) -> list[DestinationEndpointErrorKind]:
    return [
        *(
            [DestinationEndpointErrorKind.SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *_destination_snapshot_backend_ep_errors(diag, caps, host_tools, endpoint),
        *(
            [DestinationEndpointErrorKind.NOT_WRITABLE]
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
) -> list[DestinationEndpointErrorKind]:
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
) -> list[DestinationEndpointErrorKind]:
    """
    Return the btrfs capability errors of a destination endpoint.

    Tool availability (btrfs, stat, findmnt) errors are reported at the SSH endpoint
    level. Here we check capability-gated errors: the endpoint config requires btrfs but
    the volume may not support it.
    """
    match (caps, host_tools):
        case (None, _) | (_, None):
            return []
        case (_, HostToolCapabilities(has_stat=False)):
            # Can't determine filesystem type — stat error at SSH endpoint level
            return []
        case (VolumeCapabilities(is_btrfs_filesystem=None), _):
            return [DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN]
        case (VolumeCapabilities(is_btrfs_filesystem=False), _):
            return [DestinationEndpointErrorKind.VOL_NOT_BTRFS]
        case (VolumeCapabilities() as vol_caps, HostToolCapabilities() as tools):
            return _btrfs_volume_ep_errors(diag, vol_caps, tools)
        case _:
            return []


def _btrfs_volume_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    host_tools: HostToolCapabilities,
) -> list[DestinationEndpointErrorKind]:
    """Btrfs endpoint errors once the volume is known to be on btrfs."""
    return [
        *(
            [DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM]
            if host_tools.has_findmnt and not caps.btrfs_user_subvol_rm
            else []
        ),
        *_btrfs_staging_ep_errors(diag),
        *(
            [DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME]
            if diag.btrfs is not None
            and diag.btrfs.staging_exists
            and not diag.btrfs.staging_is_subvolume
            else []
        ),
        *_snapshot_dirs_ep_errors(diag),
    ]


def _btrfs_staging_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointErrorKind]:
    if diag.btrfs is None:
        return []
    if not diag.btrfs.staging_exists:
        return [DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND]
    elif diag.btrfs.staging_writable is False:
        return [DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_WRITABLE]
    else:
        return []


def _hardlink_destination_ep_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities | None,
    host_tools: HostToolCapabilities | None,
) -> list[DestinationEndpointErrorKind]:
    if caps is None or host_tools is None:
        return []
    if not host_tools.has_stat:
        # stat error handled at SSH endpoint level
        return []
    else:
        return [*_hardlink_support_ep_errors(caps), *_snapshot_dirs_ep_errors(diag)]


def _hardlink_support_ep_errors(
    caps: VolumeCapabilities,
) -> list[DestinationEndpointErrorKind]:
    match caps.hardlink_supported:
        case None:
            return [DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN]
        case False:
            return [DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT]
        case True:
            return []


def _snapshot_dirs_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointErrorKind]:
    sd = diag.snapshot_dirs
    if sd is None:
        return []
    elif not sd.exists:
        return [DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND]
    elif sd.writable is False:
        return [DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_WRITABLE]
    else:
        return []


def _destination_latest_ep_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[DestinationEndpointErrorKind]:
    latest = diag.latest
    if latest is None or not latest.exists:
        return [DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND]
    elif latest.target_valid is False:
        return [DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID]
    else:
        return []
