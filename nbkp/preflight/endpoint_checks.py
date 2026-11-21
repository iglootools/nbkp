"""No ``SyncErrorKind`` interpretation happens here — the sync layer translates
diagnostics + capabilities into errors.

``VolumeCapabilities`` is accepted to decide *which* checks to run
(e.g. btrfs subvolume only when the filesystem is btrfs), avoiding
wasteful remote commands.
"""

from ..config import (
    SyncEndpoint,
    Volume,
    resolve_endpoint_path,
)
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    Snapshot,
)
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoints
from .probes import (
    check_directory_exists,
    check_directory_writable,
    check_endpoint_sentinel,
    check_symlink_exists,
    read_symlink_target,
)
from .snapshot_checks import check_btrfs_subvolume, probe_btrfs_readonly
from .status.endpoint import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
)
from .status.ssh import HostToolCapabilities
from .status.volume import VolumeCapabilities


def observe_source_endpoint(
    endpoint: SyncEndpoint,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> SourceEndpointDiagnostics:
    sentinel_exists = check_endpoint_sentinel(
        volume, endpoint.subdir, SOURCE_SENTINEL, resolved_endpoints, run=run
    )
    src_ep = resolve_endpoint_path(volume, endpoint.subdir)

    return SourceEndpointDiagnostics(
        endpoint_slug=endpoint.slug,
        sentinel_exists=sentinel_exists,
        **(
            {
                "snapshot_dirs": _check_snapshot_dirs(
                    volume, src_ep, resolved_endpoints, run=run
                ),
                "latest": _read_latest_state(
                    volume, src_ep, resolved_endpoints, run=run
                ),
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
    *,
    host_tools: HostToolCapabilities,
    run: VolumeCommandRunner = run_on_volume,
) -> DestinationEndpointDiagnostics:
    sentinel_exists = check_endpoint_sentinel(
        volume, endpoint.subdir, DESTINATION_SENTINEL, resolved_endpoints, run=run
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
            volume, dst_ep, resolved_endpoints, run=run
        ),
        btrfs=_check_btrfs_diagnostics(
            endpoint,
            volume,
            capabilities,
            dst_ep,
            resolved_endpoints,
            host_tools,
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
                volume, f"{dst_ep}/{latest.raw_target}", resolved_endpoints, run=run
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
    host_tools: HostToolCapabilities,
    *,
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
        volume, staging_path, resolved_endpoints, run=run
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
                    volume, f"{endpoint_path}/{target}", resolved_endpoints, run=run
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
    """The snapshot *target* designates, if it is one of this endpoint's."""
    try:
        return Snapshot.from_latest_target(target)
    except ValueError:
        return None
