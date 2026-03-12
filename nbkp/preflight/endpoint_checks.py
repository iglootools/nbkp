"""Sync-endpoint-level checks (source and destination).

Each function checks subdir-dependent properties of an endpoint,
reading host/volume-level capabilities from the pre-computed
``VolumeCapabilities`` to avoid redundant remote commands.
"""

from __future__ import annotations

from ..config import (
    ResolvedEndpoints,
    SyncEndpoint,
    Volume,
)
from ..conventions import DESTINATION_SENTINEL, SNAPSHOTS_DIR, SOURCE_SENTINEL
from .queries import (
    _check_directory_exists,
    _check_directory_writable,
    _check_endpoint_sentinel,
    _resolve_endpoint,
)
from .snapshot_checks import (
    _check_btrfs_subvolume,
    _check_latest_symlink,
)
from .status import (
    SyncEndpointStatus,
    SyncReason,
    VolumeCapabilities,
)


def check_source_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> SyncEndpointStatus:
    """Check a source sync endpoint.

    Validates sentinel, rsync availability, and snapshot readiness.
    The ``latest`` symlink is read and cached in ``latest_target``,
    but ``/dev/null`` interpretation is deferred to ``check_sync``.
    """
    reasons: list[SyncReason] = []
    latest_target: str | None = None

    # Sentinel
    if not _check_endpoint_sentinel(
        volume, endpoint.subdir, SOURCE_SENTINEL, resolved_endpoints
    ):
        reasons.append(SyncReason.SOURCE_SENTINEL_NOT_FOUND)

    # Rsync (from capabilities — no remote call)
    if not capabilities.has_rsync:
        reasons.append(SyncReason.SOURCE_RSYNC_NOT_FOUND)
    elif not capabilities.rsync_version_ok:
        reasons.append(SyncReason.SOURCE_RSYNC_TOO_OLD)

    # Snapshot-enabled source checks
    if endpoint.snapshot_mode != "none":
        src_ep = _resolve_endpoint(volume, endpoint.subdir)
        snaps_path = f"{src_ep}/{SNAPSHOTS_DIR}"
        if not _check_directory_exists(volume, snaps_path, resolved_endpoints):
            reasons.append(SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND)

        # Read latest symlink (raw — /dev/null interpretation deferred)
        latest_target, latest_reasons = _check_latest_symlink(
            volume,
            src_ep,
            SyncReason.SOURCE_LATEST_NOT_FOUND,
            SyncReason.SOURCE_LATEST_INVALID,
            resolved_endpoints,
        )
        reasons.extend(latest_reasons)

    return SyncEndpointStatus(
        endpoint_slug=endpoint.slug,
        role="source",
        reasons=reasons,
        latest_target=latest_target,
    )


def check_destination_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
) -> SyncEndpointStatus:
    """Check a destination sync endpoint.

    Validates sentinel, rsync availability, writability, and
    snapshot-specific filesystem/directory checks.  Reads btrfs
    and hard-link status from ``capabilities`` (no remote calls
    for host-level checks).
    """
    reasons: list[SyncReason] = []
    dst_latest_target: str | None = None

    # Sentinel
    if not _check_endpoint_sentinel(
        volume, endpoint.subdir, DESTINATION_SENTINEL, resolved_endpoints
    ):
        reasons.append(SyncReason.DESTINATION_SENTINEL_NOT_FOUND)

    # Rsync (from capabilities)
    if not capabilities.has_rsync:
        reasons.append(SyncReason.DESTINATION_RSYNC_NOT_FOUND)
    elif not capabilities.rsync_version_ok:
        reasons.append(SyncReason.DESTINATION_RSYNC_TOO_OLD)

    # Btrfs snapshot checks
    if endpoint.btrfs_snapshots.enabled:
        if not capabilities.has_btrfs:
            reasons.append(SyncReason.DESTINATION_BTRFS_NOT_FOUND)
        else:
            if not capabilities.has_stat:
                reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
            if not capabilities.has_findmnt:
                reasons.append(SyncReason.DESTINATION_FINDMNT_NOT_FOUND)

            if capabilities.has_stat:
                if not capabilities.is_btrfs_filesystem:
                    reasons.append(SyncReason.DESTINATION_NOT_BTRFS)
                elif not _check_btrfs_subvolume(
                    volume, endpoint.subdir, resolved_endpoints
                ):
                    reasons.append(SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME)
                else:
                    if (
                        capabilities.has_findmnt
                        and not capabilities.btrfs_user_subvol_rm
                    ):
                        reasons.append(
                            SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM
                        )
                    reasons.extend(
                        _check_snapshot_dirs(
                            volume, endpoint, resolved_endpoints, btrfs=True
                        )
                    )

    # Hard-link snapshot checks
    elif endpoint.hard_link_snapshots.enabled:
        if not capabilities.has_stat:
            reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
        else:
            if not capabilities.hardlink_supported:
                reasons.append(SyncReason.DESTINATION_NO_HARDLINK_SUPPORT)
            reasons.extend(
                _check_snapshot_dirs(volume, endpoint, resolved_endpoints, btrfs=False)
            )

    # Endpoint writability
    dst_ep = _resolve_endpoint(volume, endpoint.subdir)
    if not _check_directory_writable(volume, dst_ep, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_ENDPOINT_NOT_WRITABLE)

    # Destination latest symlink (snapshot modes)
    if endpoint.snapshot_mode != "none":
        dst_ep = _resolve_endpoint(volume, endpoint.subdir)
        dst_latest_target, latest_reasons = _check_latest_symlink(
            volume,
            dst_ep,
            SyncReason.DESTINATION_LATEST_NOT_FOUND,
            SyncReason.DESTINATION_LATEST_INVALID,
            resolved_endpoints,
        )
        reasons.extend(latest_reasons)

    return SyncEndpointStatus(
        endpoint_slug=endpoint.slug,
        role="destination",
        reasons=reasons,
        latest_target=dst_latest_target,
    )


def _check_snapshot_dirs(
    volume: Volume,
    endpoint: SyncEndpoint,
    resolved_endpoints: ResolvedEndpoints,
    *,
    btrfs: bool,
) -> list[SyncReason]:
    """Check snapshot directory existence and writability."""
    from ..conventions import STAGING_DIR

    reasons: list[SyncReason] = []
    ep = _resolve_endpoint(volume, endpoint.subdir)

    if btrfs:
        staging_path = f"{ep}/{STAGING_DIR}"
        if not _check_directory_exists(volume, staging_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_TMP_NOT_FOUND)
        elif not _check_directory_writable(volume, staging_path, resolved_endpoints):
            reasons.append(SyncReason.DESTINATION_STAGING_DIR_NOT_WRITABLE)

    snaps_path = f"{ep}/{SNAPSHOTS_DIR}"
    if not _check_directory_exists(volume, snaps_path, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND)
    elif not _check_directory_writable(volume, snaps_path, resolved_endpoints):
        reasons.append(SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE)

    return reasons
