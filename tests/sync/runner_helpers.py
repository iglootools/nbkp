"""Shared builders for the sync runner tests: statuses, configs, a fixed clock."""

from datetime import UTC, datetime
from typing import Any

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
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
from nbkp.sync import RunOptions, SyncResult, run_all_syncs

FIXED_NOW = datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC)
# The snapshot a real run names from FIXED_NOW on a remote destination.
NEW_SNAPSHOT_NAME = "2026-03-06T14:30:00.000Z"


def run_syncs(
    config: Config, sync_statuses: dict[str, SyncStatus], **kwargs: Any
) -> list[SyncResult]:
    """run_all_syncs with a fixed clock and platform.

    Pass ``rsync=`` and ``run_command=`` fakes (``tests.fakes``) for any sync
    that is attempted: the defaults are the real rsync and transport.
    """
    callbacks = {k: v for k, v in kwargs.items() if k in _CALLBACKS}
    options = {k: v for k, v in kwargs.items() if k not in _CALLBACKS}
    return run_all_syncs(
        config,
        sync_statuses,
        RunOptions(clock=lambda: FIXED_NOW, platform="linux", **options),
        **callbacks,
    )


_CALLBACKS = frozenset({"on_rsync_output", "on_sync_start", "on_sync_end"})


def active_ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
        errors=[],
    )


def inactive_ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
        errors=[SshEndpointErrorKind.UNREACHABLE],
    )


def active_vol_status(name: str, vol: Volume) -> VolumeStatus:
    return VolumeStatus(
        slug=name,
        config=vol,
        ssh_endpoint_status=active_ssh_status(),
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )


def inactive_vol_status(name: str, vol: Volume) -> VolumeStatus:
    return VolumeStatus(
        slug=name,
        config=vol,
        ssh_endpoint_status=inactive_ssh_status(),
        diagnostics=None,
        errors=[VolumeErrorKind.SSH_ENDPOINT_INACTIVE],
    )


def active_src_ep_status(config: Config, sync: SyncConfig) -> SourceEndpointStatus:
    ep = config.source_endpoint(sync)
    vol = config.volumes[ep.volume]
    return SourceEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=active_vol_status(ep.volume, vol),
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=ep.slug,
            sentinel_exists=True,
        ),
        errors=[],
    )


def active_dst_ep_status(config: Config, sync: SyncConfig) -> DestinationEndpointStatus:
    ep = config.destination_endpoint(sync)
    vol = config.volumes[ep.volume]
    return DestinationEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=active_vol_status(ep.volume, vol),
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug=ep.slug,
            sentinel_exists=True,
            endpoint_writable=True,
        ),
        errors=[],
    )


def inactive_src_ep_status(config: Config, sync: SyncConfig) -> SourceEndpointStatus:
    ep = config.source_endpoint(sync)
    vol = config.volumes[ep.volume]
    return SourceEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=inactive_vol_status(ep.volume, vol),
        diagnostics=None,
        errors=[SourceEndpointErrorKind.VOLUME_INACTIVE],
    )


def inactive_dst_ep_status(
    config: Config, sync: SyncConfig
) -> DestinationEndpointStatus:
    ep = config.destination_endpoint(sync)
    vol = config.volumes[ep.volume]
    return DestinationEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=inactive_vol_status(ep.volume, vol),
        diagnostics=None,
        errors=[DestinationEndpointErrorKind.VOLUME_INACTIVE],
    )


def make_local_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={"s1": sync},
    )


def make_snapshot_config(
    *,
    btrfs: BtrfsSnapshotConfig | None = None,
    hard_link: HardLinkSnapshotConfig | None = None,
) -> Config:
    """Local source, snapshot-enabled destination ``/dst`` on a remote host.

    Remote, so every destination operation — including the ``latest``
    symlink, which is a local filesystem call on a local volume — goes
    through the injected command runner.
    """
    nas = SshEndpoint(slug="nas", host="nas.local")
    return Config(
        ssh_endpoints={"nas": nas},
        volumes={
            "src": LocalVolume(slug="src", path="/src"),
            "dst": RemoteVolume(slug="dst", ssh_endpoint="nas", path="/dst"),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=btrfs or BtrfsSnapshotConfig(),
                hard_link_snapshots=hard_link or HardLinkSnapshotConfig(),
            ),
        },
        syncs={"s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst")},
    )


def make_remote_same_server_btrfs_config() -> Config:
    server = SshEndpoint(slug="server", host="nas.local", user="backup")
    src = RemoteVolume(
        slug="src",
        ssh_endpoint="server",
        path="/data",
    )
    dst = RemoteVolume(
        slug="dst",
        ssh_endpoint="server",
        path="/backup",
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        ssh_endpoints={"server": server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )


def active_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=active_src_ep_status(config, sync),
            destination_endpoint_status=active_dst_ep_status(config, sync),
            errors=[],
        )
        for name, sync in config.syncs.items()
    }
    return {}, sync_statuses


def inactive_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=inactive_src_ep_status(config, sync),
            destination_endpoint_status=inactive_dst_ep_status(config, sync),
            errors=[
                SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
                SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
            ],
        )
        for name, sync in config.syncs.items()
    }
    return {}, sync_statuses
