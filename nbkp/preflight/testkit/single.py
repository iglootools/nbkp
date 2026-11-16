"""Preflight one sync or one volume through the production pipeline."""

from ...config import Config, LocalVolume, RemoteVolume, SshEndpoint, SyncConfig, Volume
from ...remote.endpoints import ResolvedEndpoints
from .. import SyncStatus, VolumeStatus, check_all_syncs


def check_sync(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> SyncStatus:
    result = check_all_syncs(
        config,
        only_syncs=[sync.slug],
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
    )
    return result.sync_statuses[sync.slug]


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeStatus:
    """Preflight a config holding only *volume*.

    A remote volume's SSH endpoint is a stand-in: it only has to exist for the
    config to validate, since the volume's probes connect through
    *resolved_endpoints*.
    """
    config = Config(
        ssh_endpoints=_stand_in_endpoint(volume),
        volumes={volume.slug: volume},
    )
    result = check_all_syncs(config, resolved_endpoints=resolved_endpoints)
    return result.volume_statuses[volume.slug]


def _stand_in_endpoint(volume: Volume) -> dict[str, SshEndpoint]:
    match volume:
        case LocalVolume():
            return {}
        case RemoteVolume():
            slug = volume.ssh_endpoint
            return {slug: SshEndpoint(slug=slug, host=slug)}
