"""Sync-endpoint-level diagnostics (source and destination).

Each function observes subdir-dependent state of an endpoint and
returns a diagnostics model.  No ``SyncError`` interpretation
happens here — the sync layer translates diagnostics + capabilities
into errors.

``VolumeCapabilities`` is accepted to decide *which* checks to run
(e.g. btrfs subvolume only when the filesystem is btrfs), avoiding
wasteful remote commands.
"""

from __future__ import annotations

from ..config import (
    SyncEndpoint,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    Snapshot,
)
from .queries import (
    _check_directory_writable,
    _check_endpoint_sentinel,
    _check_symlink_exists,
    check_directory_exists,
    read_symlink_target,
    resolve_endpoint,
)
from .snapshot_checks import check_btrfs_subvolume
from .status import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    VolumeCapabilities,
)


def observe_source_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> SourceEndpointDiagnostics:
    """Observe state of a source sync endpoint."""
    sentinel_exists = _check_endpoint_sentinel(
        volume, endpoint.subdir, SOURCE_SENTINEL, resolved_endpoints
    )
    src_ep = resolve_endpoint(volume, endpoint.subdir)

    return SourceEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        **(
            {
                "snapshot_dirs": _check_snapshot_dirs(
                    volume, src_ep, resolved_endpoints
                ),
                "latest": _read_latest_state(volume, src_ep, resolved_endpoints),
            }
            if endpoint.snapshot_mode != "none"
            else {}
        ),
    )


def observe_destination_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> DestinationEndpointDiagnostics:
    """Observe state of a destination sync endpoint."""
    sentinel_exists = _check_endpoint_sentinel(
        volume, endpoint.subdir, DESTINATION_SENTINEL, resolved_endpoints
    )
    dst_ep = resolve_endpoint(volume, endpoint.subdir)

    return DestinationEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        endpoint_writable=_check_directory_writable(volume, dst_ep, resolved_endpoints),
        btrfs=_check_btrfs_diagnostics(
            endpoint, volume, capabilities, dst_ep, resolved_endpoints
        ),
        **(
            {
                "snapshot_dirs": _check_snapshot_dirs(
                    volume, dst_ep, resolved_endpoints
                ),
                "latest": _read_latest_state(volume, dst_ep, resolved_endpoints),
            }
            if endpoint.snapshot_mode != "none"
            else {}
        ),
    )


def _check_btrfs_diagnostics(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    dst_ep: str,
    resolved_endpoints: ResolvedEndpoints,
) -> BtrfsStagingSubvolumeDiagnostics | None:
    """Check btrfs staging subvolume state."""
    if not (
        endpoint.btrfs_snapshots.enabled
        and capabilities.has_stat
        and capabilities.is_btrfs_filesystem
    ):
        return None

    staging_subdir = (
        f"{endpoint.subdir}/{STAGING_DIR}" if endpoint.subdir else STAGING_DIR
    )
    staging_path = f"{dst_ep}/{STAGING_DIR}"
    staging_exists = check_directory_exists(volume, staging_path, resolved_endpoints)
    return BtrfsStagingSubvolumeDiagnostics(
        staging_exists=staging_exists,
        staging_is_subvolume=(
            check_btrfs_subvolume(volume, staging_subdir, resolved_endpoints)
            if staging_exists
            else False
        ),
        staging_writable=(
            _check_directory_writable(volume, staging_path, resolved_endpoints)
            if staging_exists
            else None
        ),
    )


# ── Helpers ─────────────────────────────────────────────────


def _check_snapshot_dirs(
    volume: Volume,
    endpoint_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> SnapshotDirsDiagnostics:
    """Check snapshot directory existence and writability."""
    snaps_path = f"{endpoint_path}/{SNAPSHOTS_DIR}"
    exists = check_directory_exists(volume, snaps_path, resolved_endpoints)
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
    if not _check_symlink_exists(volume, latest_path, resolved_endpoints):
        return LatestSymlinkState(exists=False)

    raw_target = read_symlink_target(volume, latest_path, resolved_endpoints)
    if raw_target is None:
        return LatestSymlinkState(exists=False)
    else:
        target = str(raw_target)
        if target == DEVNULL_TARGET:
            return LatestSymlinkState(exists=True, raw_target=target)
        else:
            resolved = f"{endpoint_path}/{target}"
            target_valid = check_directory_exists(volume, resolved, resolved_endpoints)
            name = target.rsplit("/", 1)[-1] if "/" in target else target
            return LatestSymlinkState(
                exists=True,
                raw_target=target,
                target_valid=target_valid,
                snapshot=(Snapshot.from_name(name) if target_valid else None),
            )
