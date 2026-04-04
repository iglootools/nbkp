"""Shared CLI test helpers: status builders, config fixtures, and runner."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    HostToolCapabilities,
    PreflightResult,
    SourceEndpointDiagnostics,
    SourceEndpointError,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointError,
    SshEndpointStatus,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)

runner = CliRunner()


def strip_panel(text: str) -> str:
    """Strip Rich panel border characters and normalize whitespace."""
    text = re.sub(r"[╭╮╰╯│─]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Helper: build the 4-layer chain for tests ───────────────


def localhost_ssh_status(*, active: bool = True) -> SshEndpointStatus:
    """Build an implicit localhost SSH endpoint status."""
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
            errors=[SshEndpointError.UNREACHABLE],
        )


def remote_ssh_status(
    slug: str = "nas-server",
    *,
    active: bool = True,
    rsync_missing: bool = False,
) -> SshEndpointStatus:
    """Build a remote SSH endpoint status."""
    if not active:
        return SshEndpointStatus(
            slug=slug,
            diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
            errors=[SshEndpointError.UNREACHABLE],
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
            errors=[SshEndpointError.RSYNC_NOT_FOUND],
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
    """Build a VolumeStatus with the 4-layer chain."""
    if not ssh_status.active:
        return VolumeStatus(
            slug=slug,
            config=config.volumes[slug],
            ssh_endpoint_status=ssh_status,
            diagnostics=None,
            errors=[VolumeError.SSH_ENDPOINT_INACTIVE],
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
        errors = [VolumeError.SENTINEL_NOT_FOUND] if not sentinel_exists else []
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
    """Build a SourceEndpointStatus."""
    if not vol_s.active:
        return SourceEndpointStatus(
            endpoint_slug=endpoint_slug,
            volume_status=vol_s,
            diagnostics=None,
            errors=[SourceEndpointError.VOLUME_INACTIVE],
        )
    diag = SourceEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=sentinel_exists,
    )
    errors = [SourceEndpointError.SENTINEL_NOT_FOUND] if not sentinel_exists else []
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
) -> DestinationEndpointStatus:
    """Build a DestinationEndpointStatus."""
    if not vol_s.active:
        return DestinationEndpointStatus(
            endpoint_slug=endpoint_slug,
            volume_status=vol_s,
            diagnostics=None,
            errors=[DestinationEndpointError.VOLUME_INACTIVE],
        )
    diag = DestinationEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=sentinel_exists,
        endpoint_writable=True,
    )
    errors = (
        [DestinationEndpointError.SENTINEL_NOT_FOUND] if not sentinel_exists else []
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
    ssh_statuses: dict[str, SshEndpointStatus] = {}
    for vs in vol_statuses.values():
        ssh_s = vs.ssh_endpoint_status
        if ssh_s.slug not in ssh_statuses:
            ssh_statuses[ssh_s.slug] = ssh_s
    src_ep_statuses = {
        ss.source_endpoint_status.endpoint_slug: ss.source_endpoint_status
        for ss in sync_statuses.values()
    }
    dst_ep_statuses = {
        ss.destination_endpoint_status.endpoint_slug: ss.destination_endpoint_status
        for ss in sync_statuses.values()
    }
    return PreflightResult(
        ssh_endpoint_statuses=ssh_statuses,
        volume_statuses=vol_statuses,
        source_endpoint_statuses=src_ep_statuses,
        destination_endpoint_statuses=dst_ep_statuses,
        sync_statuses=sync_statuses,
    )


# ── Test config builders ─────────────────────────────────────


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


def sample_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    """NAS unreachable -> sync inactive due to SSH-level error."""
    src_ep = src_ep_status("ep-src", vol_statuses["local-data"])
    dst_ep = dst_ep_status("ep-dst", vol_statuses["nas"])
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_endpoint_status=src_ep,
            destination_endpoint_status=dst_ep,
            errors=[SyncError.DESTINATION_ENDPOINT_INACTIVE],
        ),
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
            errors=[SyncError.DESTINATION_ENDPOINT_INACTIVE],
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
                SyncError.SOURCE_ENDPOINT_INACTIVE,
                SyncError.DESTINATION_ENDPOINT_INACTIVE,
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


def config_with_locations() -> Config:
    """Config with location-tagged SSH endpoints for filter validation tests."""
    server_home = SshEndpoint(slug="nas-home", host="192.168.1.50", location="home")
    server_travel = SshEndpoint(
        slug="nas-travel", host="nas.example.com", location="travel"
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
