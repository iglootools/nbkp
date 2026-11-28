"""
Observe sync endpoints, without interpreting what is an error.

No ``SyncErrorKind`` interpretation happens here: the sync layer translates diagnostics
and capabilities into errors.

``VolumeCapabilities`` is accepted to decide *which* checks to run
(e.g. btrfs subvolume only when the filesystem is btrfs), avoiding
wasteful remote commands.
"""

from nbkp.config import SyncEndpoint, Volume, resolve_endpoint_path
from nbkp.fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    Snapshot,
)
from nbkp.preflight.probes import (
    VolumeReadError,
    check_directory_exists,
    check_directory_writable,
    check_endpoint_sentinel,
    check_symlink_exists,
    read_symlink_target,
)
from nbkp.preflight.snapshot_checks import check_btrfs_subvolume, probe_btrfs_readonly
from nbkp.preflight.status.endpoint import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
)
from nbkp.preflight.status.ssh import HostToolCapabilities
from nbkp.preflight.status.volume import VolumeCapabilities
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints


def observe_source_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> SourceEndpointDiagnostics:
    """
    Return what the source endpoint's directory holds.

    Looks for the source sentinel, and, when the endpoint has snapshots, its snapshot
    directories and ``latest`` symlink. When the sentinel's presence cannot be told,
    nothing else is probed: the rest of the directory would be as unreadable.
    """
    try:
        sentinel_exists = check_endpoint_sentinel(
            volume,
            endpoint.subdir,
            SOURCE_SENTINEL,
            resolved_endpoints,
            run=run,
        )
    except VolumeReadError as e:
        return SourceEndpointDiagnostics(
            endpoint_slug=endpoint.slug,
            sentinel_exists=False,
            sentinel_unreadable=e.detail,
        )
    src_ep = resolve_endpoint_path(volume, endpoint.subdir)
    has_snapshots = endpoint.snapshot_mode != "none"

    return SourceEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        snapshot_dirs=(
            _check_snapshot_dirs(volume, src_ep, resolved_endpoints, run=run)
            if has_snapshots
            else None
        ),
        latest=(
            _read_latest_state(volume, src_ep, resolved_endpoints, run=run)
            if has_snapshots
            else None
        ),
    )


def observe_destination_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    resolved_endpoints: ResolvedEndpoints,
    *,
    host_tools: HostToolCapabilities,
    run: VolumeCommandRunner = run_on_volume,
) -> DestinationEndpointDiagnostics:
    """
    Return what the destination endpoint's directory holds and allows.

    Looks for the destination sentinel and whether the directory is writable, and,
    per snapshot mode, the btrfs staging subvolume, snapshot directories, ``latest``
    symlink and whether the latest btrfs snapshot is read-only. *capabilities* and
    *host_tools* skip the probes the volume or its host cannot answer. When the
    sentinel's presence cannot be told, nothing else is probed.
    """
    try:
        sentinel_exists = check_endpoint_sentinel(
            volume,
            endpoint.subdir,
            DESTINATION_SENTINEL,
            resolved_endpoints,
            run=run,
        )
    except VolumeReadError as e:
        return DestinationEndpointDiagnostics(
            endpoint_slug=endpoint.slug,
            sentinel_exists=False,
            sentinel_unreadable=e.detail,
            endpoint_writable=False,
        )
    dst_ep = resolve_endpoint_path(volume, endpoint.subdir)
    latest = (
        _read_latest_state(volume, dst_ep, resolved_endpoints, run=run)
        if endpoint.snapshot_mode != "none"
        else None
    )

    return DestinationEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        endpoint_writable=check_directory_writable(
            volume,
            dst_ep,
            resolved_endpoints,
            run=run,
        ),
        btrfs=_check_btrfs_diagnostics(
            endpoint,
            volume,
            capabilities,
            dst_ep,
            resolved_endpoints,
            host_tools=host_tools,
            run=run,
        ),
        snapshot_dirs=(
            _check_snapshot_dirs(volume, dst_ep, resolved_endpoints, run=run)
            if endpoint.snapshot_mode != "none"
            else None
        ),
        latest=latest,
        latest_snapshot_readonly=(
            probe_btrfs_readonly(
                volume,
                f"{dst_ep}/{latest.raw_target}",
                resolved_endpoints,
                run=run,
            )
            if endpoint.btrfs_snapshots.enabled
            and host_tools.has_btrfs
            and capabilities.is_btrfs_filesystem
            and latest is not None
            and latest.snapshot is not None
            else None
        ),
    )


def _check_btrfs_diagnostics(
    endpoint: SyncEndpoint,
    volume: Volume,
    capabilities: VolumeCapabilities,
    dst_ep: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    host_tools: HostToolCapabilities,
    run: VolumeCommandRunner,
) -> BtrfsStagingSubvolumeDiagnostics | None:
    if not (
        endpoint.btrfs_snapshots.enabled
        and host_tools.has_stat
        and capabilities.is_btrfs_filesystem
    ):
        return None

    staging_subdir = (
        f"{endpoint.subdir}/{STAGING_DIR}" if endpoint.subdir else STAGING_DIR
    )
    staging_path = f"{dst_ep}/{STAGING_DIR}"
    staging_exists = check_directory_exists(
        volume,
        staging_path,
        resolved_endpoints,
        run=run,
    )
    return BtrfsStagingSubvolumeDiagnostics(
        staging_exists=staging_exists,
        staging_is_subvolume=(
            check_btrfs_subvolume(volume, staging_subdir, resolved_endpoints, run=run)
            if staging_exists
            else False
        ),
        staging_writable=(
            check_directory_writable(volume, staging_path, resolved_endpoints, run=run)
            if staging_exists
            else None
        ),
    )


def _check_snapshot_dirs(
    volume: Volume,
    endpoint_path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> SnapshotDirsDiagnostics:
    snaps_path = f"{endpoint_path}/{SNAPSHOTS_DIR}"
    exists = check_directory_exists(volume, snaps_path, resolved_endpoints, run=run)
    writable = (
        check_directory_writable(volume, snaps_path, resolved_endpoints, run=run)
        if exists
        else None
    )
    return SnapshotDirsDiagnostics(exists=exists, writable=writable)


def _read_latest_state(
    volume: Volume,
    endpoint_path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> LatestSymlinkState:
    latest_path = f"{endpoint_path}/{LATEST_LINK}"
    if not check_symlink_exists(volume, latest_path, resolved_endpoints, run=run):
        return LatestSymlinkState(exists=False)

    raw_target = read_symlink_target(volume, latest_path, resolved_endpoints, run=run)
    match raw_target:
        case None:
            return LatestSymlinkState(exists=False)
        case target if target == DEVNULL_TARGET:
            return LatestSymlinkState(exists=True, raw_target=DEVNULL_TARGET)
        case target:
            snapshot = (
                _parse_snapshot(target)
                if check_directory_exists(
                    volume,
                    f"{endpoint_path}/{target}",
                    resolved_endpoints,
                    run=run,
                )
                else None
            )
            # An existing directory whose name is not a snapshot timestamp
            # (``latest -> snapshots/foo``) is as invalid as a missing one.
            return LatestSymlinkState(
                exists=True,
                raw_target=target,
                target_valid=snapshot is not None,
                snapshot=snapshot,
            )


def _parse_snapshot(target: str) -> Snapshot | None:
    """Return the snapshot *target* designates, if it is one of this endpoint's."""
    try:
        return Snapshot.from_latest_target(target)
    except ValueError:
        return None
