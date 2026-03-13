"""Sync-endpoint-level diagnostics (source and destination).

Each function observes subdir-dependent state of an endpoint and
returns a diagnostics model.  No ``SyncReason`` interpretation
happens here — the sync layer translates diagnostics + capabilities
into reasons.

``VolumeCapabilities`` is accepted to decide *which* checks to run
(e.g. btrfs subvolume only when the filesystem is btrfs), avoiding
wasteful remote commands.
"""

from __future__ import annotations

from ..config import (
    ResolvedEndpoints,
    SyncEndpoint,
    Volume,
)
from ..conventions import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
)
from .queries import (
    _check_directory_exists,
    _check_directory_writable,
    _check_endpoint_sentinel,
    _check_symlink_exists,
    _read_symlink_target,
    _resolve_endpoint,
)
from .snapshot_checks import _check_btrfs_subvolume
from .status import (
    BtrfsSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    VolumeCapabilities,
)


def check_source_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> SourceEndpointDiagnostics:
    """Observe state of a source sync endpoint."""
    sentinel_exists = _check_endpoint_sentinel(
        volume, endpoint.subdir, SOURCE_SENTINEL, resolved_endpoints
    )

    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    latest: LatestSymlinkState | None = None

    if endpoint.snapshot_mode != "none":
        src_ep = _resolve_endpoint(volume, endpoint.subdir)
        snapshot_dirs = _check_snapshot_dirs(volume, src_ep, resolved_endpoints)
        latest = _read_latest_state(volume, src_ep, resolved_endpoints)

    return SourceEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        snapshot_dirs=snapshot_dirs,
        latest=latest,
    )


def check_destination_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> DestinationEndpointDiagnostics:
    """Observe state of a destination sync endpoint."""
    sentinel_exists = _check_endpoint_sentinel(
        volume, endpoint.subdir, DESTINATION_SENTINEL, resolved_endpoints
    )

    dst_ep = _resolve_endpoint(volume, endpoint.subdir)
    endpoint_writable = _check_directory_writable(volume, dst_ep, resolved_endpoints)

    # Btrfs diagnostics (subvolume + staging dir)
    btrfs: BtrfsSubvolumeDiagnostics | None = None
    if (
        endpoint.btrfs_snapshots.enabled
        and capabilities.has_stat
        and capabilities.is_btrfs_filesystem
    ):
        is_subvolume = _check_btrfs_subvolume(
            volume, endpoint.subdir, resolved_endpoints
        )
        staging_path = f"{dst_ep}/{STAGING_DIR}"
        staging_exists = _check_directory_exists(
            volume, staging_path, resolved_endpoints
        )
        staging_writable = (
            _check_directory_writable(volume, staging_path, resolved_endpoints)
            if staging_exists
            else None
        )
        btrfs = BtrfsSubvolumeDiagnostics(
            is_subvolume=is_subvolume,
            staging_dir_exists=staging_exists,
            staging_dir_writable=staging_writable,
        )

    # Snapshot dirs (both btrfs and hard-link)
    snapshot_dirs: SnapshotDirsDiagnostics | None = None
    if endpoint.snapshot_mode != "none":
        snapshot_dirs = _check_snapshot_dirs(volume, dst_ep, resolved_endpoints)

    # Latest symlink
    latest: LatestSymlinkState | None = None
    if endpoint.snapshot_mode != "none":
        latest = _read_latest_state(volume, dst_ep, resolved_endpoints)

    return DestinationEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        endpoint_writable=endpoint_writable,
        btrfs=btrfs,
        snapshot_dirs=snapshot_dirs,
        latest=latest,
    )


# ── Helpers ─────────────────────────────────────────────────


def _check_snapshot_dirs(
    volume: Volume,
    endpoint_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> SnapshotDirsDiagnostics:
    """Check snapshot directory existence and writability."""
    snaps_path = f"{endpoint_path}/{SNAPSHOTS_DIR}"
    exists = _check_directory_exists(volume, snaps_path, resolved_endpoints)
    writable = (
        _check_directory_writable(volume, snaps_path, resolved_endpoints)
        if exists
        else None
    )
    return SnapshotDirsDiagnostics(exists=exists, writable=writable)


def _read_latest_state(
    volume: Volume,
    endpoint_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> LatestSymlinkState:
    """Read the latest symlink and return its observed state."""
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    exists = _check_symlink_exists(volume, latest_path, resolved_endpoints)
    if not exists:
        return LatestSymlinkState(exists=False)

    raw_target = _read_symlink_target(volume, latest_path, resolved_endpoints)
    if raw_target is None:
        return LatestSymlinkState(exists=False)

    target = str(raw_target)
    if target == DEVNULL_TARGET:
        return LatestSymlinkState(exists=True, raw_target=target)

    resolved = f"{endpoint_path}/{target}"
    target_valid = _check_directory_exists(volume, resolved, resolved_endpoints)
    snapshot_name = target.rsplit("/", 1)[-1] if target_valid else None

    return LatestSymlinkState(
        exists=True,
        raw_target=target,
        target_valid=target_valid,
        snapshot_name=snapshot_name,
    )
