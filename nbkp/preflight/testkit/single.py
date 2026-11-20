"""Preflight one sync or one volume through the production pipeline."""

import shutil

from ...config import Config, LocalVolume, RemoteVolume, SshEndpoint, SyncConfig, Volume
from ...remote.dispatch import VolumeCommandRunner, WhichFn, run_on_volume
from ...remote.endpoints import ResolvedEndpoints
from .. import SyncStatus, VolumeStatus, check_all_syncs


def check_sync(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    *,
    run: VolumeCommandRunner = run_on_volume,
    which: WhichFn = shutil.which,
) -> SyncStatus:
    result = check_all_syncs(
        config,
        only_syncs=[sync.slug],
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        run=run,
        which=which,
    )
    return result.sync_statuses[sync.slug]


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
    *,
    run: VolumeCommandRunner = run_on_volume,
    which: WhichFn = shutil.which,
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
    result = check_all_syncs(
        config, resolved_endpoints=resolved_endpoints, run=run, which=which
    )
    return result.volume_statuses[volume.slug]


def _stand_in_endpoint(volume: Volume) -> dict[str, SshEndpoint]:
    match volume:
        case LocalVolume():
            return {}
        case RemoteVolume():
            slug = volume.ssh_endpoint
            return {slug: SshEndpoint(slug=slug, host=slug)}
