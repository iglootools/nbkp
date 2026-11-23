"""Shared test fixtures: preflight status builders and sample configs."""

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    HostToolCapabilities,
    LatestSymlinkState,
    PreflightResult,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from nbkp.preflight.testkit.common import assemble_preflight_result


def localhost_ssh_status(*, active: bool = True) -> SshEndpointStatus:
    if active:
        return SshEndpointStatus(
            slug="localhost",
            diagnostics=SshEndpointDiagnostics(
                ssh_reachable=None,
                host_tools=HostToolCapabilities(
                    has_rsync=True,
                    rsync_version_ok=True,
                    has_btrfs=False,
                    has_stat=True,
                    has_findmnt=False,
                ),
            ),
            errors=[],
        )
    else:
        return SshEndpointStatus(
            slug="localhost",
            diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
            errors=[SshEndpointErrorKind.UNREACHABLE],
        )


def remote_ssh_status(
    slug: str = "nas-server",
    *,
    active: bool = True,
    rsync_missing: bool = False,
) -> SshEndpointStatus:
    if not active:
        return SshEndpointStatus(
            slug=slug,
            diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
            errors=[SshEndpointErrorKind.UNREACHABLE],
        )
    elif rsync_missing:
        return SshEndpointStatus(
            slug=slug,
            diagnostics=SshEndpointDiagnostics(
                ssh_reachable=True,
                host_tools=HostToolCapabilities(
                    has_rsync=False,
                    rsync_version_ok=False,
                    has_btrfs=False,
                    has_stat=True,
                    has_findmnt=False,
                ),
            ),
            errors=[SshEndpointErrorKind.RSYNC_NOT_FOUND],
        )
    else:
        return SshEndpointStatus(
            slug=slug,
            diagnostics=SshEndpointDiagnostics(
                ssh_reachable=True,
                host_tools=HostToolCapabilities(
                    has_rsync=True,
                    rsync_version_ok=True,
                    has_btrfs=False,
                    has_stat=True,
                    has_findmnt=False,
                ),
            ),
            errors=[],
        )


def vol_status(
    slug: str,
    config: Config,
    ssh_status: SshEndpointStatus,
    *,
    sentinel_exists: bool = True,
) -> VolumeStatus:
    if not ssh_status.active:
        return VolumeStatus(
            slug=slug,
            config=config.volumes[slug],
            ssh_endpoint_status=ssh_status,
            diagnostics=None,
            errors=[VolumeErrorKind.SSH_ENDPOINT_INACTIVE],
        )
    else:
        diag = VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=sentinel_exists,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        )
        errors = [VolumeErrorKind.SENTINEL_NOT_FOUND] if not sentinel_exists else []
        return VolumeStatus(
            slug=slug,
            config=config.volumes[slug],
            ssh_endpoint_status=ssh_status,
            diagnostics=diag,
            errors=errors,
        )


def src_ep_status(
    endpoint_slug: str,
    vol_s: VolumeStatus,
    *,
    sentinel_exists: bool = True,
) -> SourceEndpointStatus:
    if not vol_s.active:
        return SourceEndpointStatus(
            endpoint_slug=endpoint_slug,
            volume_status=vol_s,
            diagnostics=None,
            errors=[SourceEndpointErrorKind.VOLUME_INACTIVE],
        )
    diag = SourceEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=sentinel_exists,
    )
    errors = [SourceEndpointErrorKind.SENTINEL_NOT_FOUND] if not sentinel_exists else []
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=vol_s,
        diagnostics=diag,
        errors=errors,
    )


def dst_ep_status(
    endpoint_slug: str,
    vol_s: VolumeStatus,
    *,
    sentinel_exists: bool = True,
    latest_snapshot: str | None = None,
) -> DestinationEndpointStatus:
    """
    Build a destination endpoint status.

    *latest_snapshot* is a snapshot directory name (e.g. ``2026-03-06T14:30:00.000Z``);
    passing one populates the ``latest`` symlink state with a parsed :class:`Snapshot`,
    whose ``timestamp`` is a ``datetime``. That field is the one non-JSON-native type
    reachable from a status tree, so it is what exercises JSON serialization.
    """
    if not vol_s.active:
        return DestinationEndpointStatus(
            endpoint_slug=endpoint_slug,
            volume_status=vol_s,
            diagnostics=None,
            errors=[DestinationEndpointErrorKind.VOLUME_INACTIVE],
        )
    diag = DestinationEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=sentinel_exists,
        endpoint_writable=True,
        latest=(
            None
            if latest_snapshot is None
            else LatestSymlinkState(
                exists=True,
                raw_target=f"snapshots/{latest_snapshot}",
                target_valid=True,
                snapshot=Snapshot.from_name(latest_snapshot),
            )
        ),
    )
    errors = (
        [DestinationEndpointErrorKind.SENTINEL_NOT_FOUND] if not sentinel_exists else []
    )
    return DestinationEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=vol_s,
        diagnostics=diag,
        errors=errors,
    )


def preflight(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
) -> PreflightResult:
    """Build a PreflightResult, collecting statuses from volumes and syncs."""
    return assemble_preflight_result(vol_statuses, sync_statuses)


def sync_status(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
    sync_slug: str,
    *,
    src_ep_errors: list[SourceEndpointErrorKind] | None = None,
    dst_ep_errors: list[DestinationEndpointErrorKind] | None = None,
    sync_errors: list[SyncErrorKind] | None = None,
    destination_latest_snapshot: Snapshot | None = None,
) -> SyncStatus:
    """
    Build *sync_slug*'s status over *vol_statuses*.

    Its endpoints are built by :func:`src_ep_status` and :func:`dst_ep_status` with the
    given errors added.
    """
    sync = config.syncs[sync_slug]
    src = src_ep_status(
        sync.source,
        vol_statuses[config.sync_endpoints[sync.source].volume],
    )
    dst = dst_ep_status(
        sync.destination,
        vol_statuses[config.sync_endpoints[sync.destination].volume],
    )
    return SyncStatus(
        slug=sync_slug,
        config=sync,
        source_endpoint_status=src.model_copy(
            update={"errors": [*src.errors, *(src_ep_errors or [])]},
        ),
        destination_endpoint_status=dst.model_copy(
            update={"errors": [*dst.errors, *(dst_ep_errors or [])]},
        ),
        errors=sync_errors or [],
        destination_latest_snapshot=destination_latest_snapshot,
    )


def local_vol_statuses(config: Config) -> dict[str, VolumeStatus]:
    """Every volume of *config*, active on ``localhost``."""
    ssh = localhost_ssh_status()
    return {name: vol_status(name, config, ssh) for name in config.volumes}


def btrfs_config() -> Config:
    """Build ``btrfs-sync``: local ``src`` to local ``dst``, with btrfs snapshots."""
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    ep_src = SyncEndpoint(slug="ep-src", volume="src")
    ep_dst = SyncEndpoint(
        slug="ep-dst",
        volume="dst",
        btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
    )
    sync = SyncConfig(
        slug="btrfs-sync",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"btrfs-sync": sync},
    )


def sample_config() -> Config:
    src = LocalVolume(slug="local-data", path="/mnt/data")
    nas_server = SshEndpoint(
        slug="nas-server",
        host="nas.example.com",
        port=5022,
        user="backup",
    )
    dst = RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-server",
        path="/volume1/backups",
    )
    ep_src = SyncEndpoint(slug="ep-src", volume="local-data", subdir="photos")
    ep_dst = SyncEndpoint(slug="ep-dst", volume="nas", subdir="photos-backup")
    sync = SyncConfig(
        slug="photos-to-nas",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        ssh_endpoints={"nas-server": nas_server},
        volumes={"local-data": src, "nas": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"photos-to-nas": sync},
    )


def sample_vol_statuses(
    config: Config,
) -> dict[str, VolumeStatus]:
    """Source volume active, NAS unreachable."""
    local_ssh = localhost_ssh_status()
    nas_ssh = remote_ssh_status("nas-server", active=False)
    return {
        "local-data": vol_status("local-data", config, local_ssh),
        "nas": vol_status("nas", config, nas_ssh),
    }


def sample_error_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    """NAS has rsync missing -- a real error, not expected-inactive."""
    nas_ssh = remote_ssh_status("nas-server", rsync_missing=True)
    nas_vol = vol_status("nas", config, nas_ssh)
    src_ep = src_ep_status("ep-src", vol_statuses["local-data"])
    dst_ep = dst_ep_status("ep-dst", nas_vol)
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_endpoint_status=src_ep,
            destination_endpoint_status=dst_ep,
            errors=[SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
        ),
    }


def sample_sentinel_only_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    """Both endpoint sentinels missing -- expected-inactive."""
    src = src_ep_status("ep-src", vol_statuses["local-data"], sentinel_exists=False)
    dst = dst_ep_status("ep-dst", vol_statuses["nas"], sentinel_exists=False)
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_endpoint_status=src,
            destination_endpoint_status=dst,
            errors=[
                SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
                SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
            ],
        ),
    }


def sample_all_active_vol_statuses(
    config: Config,
) -> dict[str, VolumeStatus]:
    local_ssh = localhost_ssh_status()
    nas_ssh = remote_ssh_status("nas-server")
    return {
        "local-data": vol_status("local-data", config, local_ssh),
        "nas": vol_status("nas", config, nas_ssh),
    }


def sample_all_active_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    src_ep = src_ep_status("ep-src", vol_statuses["local-data"])
    dst_ep = dst_ep_status("ep-dst", vol_statuses["nas"])
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_endpoint_status=src_ep,
            destination_endpoint_status=dst_ep,
            errors=[],
        ),
    }


def sample_sync_statuses_with_snapshots(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    """
    All-active sync statuses whose destination has a populated ``latest``.

    The only difference from :func:`sample_all_active_sync_statuses` is that
    the destination's ``latest`` symlink resolves to a real snapshot, putting
    a ``datetime`` into the status tree.  Fixtures that leave it unset make
    JSON output look serializable when it is not.
    """
    name = "2026-03-06T14:30:00.000Z"
    src_ep = src_ep_status("ep-src", vol_statuses["local-data"])
    dst_ep = dst_ep_status("ep-dst", vol_statuses["nas"], latest_snapshot=name)
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_endpoint_status=src_ep,
            destination_endpoint_status=dst_ep,
            errors=[],
            # Both routes a Snapshot reaches JSON by: nested under the
            # destination's diagnostics, and directly on the sync status.
            destination_latest_snapshot=Snapshot.from_name(name),
        ),
    }


def config_with_locations() -> Config:
    """Config with location-tagged SSH endpoints for filter validation tests."""
    server_home = SshEndpoint(slug="nas-home", host="192.168.1.50", location="home")
    server_travel = SshEndpoint(
        slug="nas-travel",
        host="nas.example.com",
        location="travel",
    )
    src = LocalVolume(slug="local-data", path="/mnt/data")
    dst = RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-home",
        ssh_endpoints=["nas-home", "nas-travel"],
        path="/volume1/backups",
    )
    ep_src = SyncEndpoint(slug="ep-src", volume="local-data")
    ep_dst = SyncEndpoint(slug="ep-dst", volume="nas")
    sync = SyncConfig(slug="backup", source="ep-src", destination="ep-dst")
    return Config(
        ssh_endpoints={"nas-home": server_home, "nas-travel": server_travel},
        volumes={"local-data": src, "nas": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"backup": sync},
    )
