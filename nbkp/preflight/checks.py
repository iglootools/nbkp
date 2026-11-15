"""Sync check orchestration.

Composes SSH endpoint diagnostics, volume diagnostics, endpoint
diagnostics, and capabilities into the primary entry points:
``check_sync`` and ``check_all_syncs``.

Four-phase check hierarchy (each level gates the next):

1. **SSH endpoints** — ``SshEndpointDiagnostics`` (observation) →
   ``SshEndpointStatus`` (interpretation via
   ``SshEndpointStatus.from_diagnostics``)
2. **Volumes** — ``VolumeDiagnostics`` (observation) →
   ``VolumeStatus`` (interpretation via
   ``VolumeStatus.from_diagnostics``)
3. **Sync endpoints** — ``SourceEndpointDiagnostics`` /
   ``DestinationEndpointDiagnostics`` (observation) →
   ``SourceEndpointStatus`` / ``DestinationEndpointStatus``
   (interpretation via ``from_diagnostics``)
4. **Syncs** — ``SyncStatus`` (interpretation via
   ``SyncStatus.from_diagnostics``)
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import partial

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from ..disks import MountObservation
from ..remote.endpoints import ResolvedEndpoints, location_excluded
from ..remote.errors import SSH_CONNECTION_ERRORS
from ..remote.resolution import enrich_from_ssh_config, resolve_proxy_chain
from .endpoint_checks import observe_destination_endpoint, observe_source_endpoint
from .severity import PreflightError
from .ssh_checks import observe_standalone_endpoint
from .status.endpoint import DestinationEndpointStatus, SourceEndpointStatus
from .status.result import PreflightResult
from .status.ssh import (
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
)
from .status.sync import SyncStatus
from .status.volume import VolumeCapabilities, VolumeStatus
from .volume_checks import (
    observe_ssh_endpoint,
    observe_volume,
    unreachable_diagnostics,
)


@dataclass(frozen=True)
class _Progress:
    """Progress callbacks; labels look like ``"vol:usb-backup"``."""

    on_start: Callable[[str], None] | None
    on_end: Callable[[str, Sequence[PreflightError]], None] | None

    def track[
        S: (
            SshEndpointStatus,
            VolumeStatus,
            SourceEndpointStatus,
            DestinationEndpointStatus,
        )
    ](self, label: str, run: Callable[[], S]) -> S:
        if self.on_start is not None:
            self.on_start(label)
        status = run()
        if self.on_end is not None:
            self.on_end(label, status.errors)
        return status


def check_all_syncs(
    config: Config,
    on_check_start: Callable[[str], None] | None = None,
    on_check_end: Callable[[str, Sequence[PreflightError]], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
) -> PreflightResult:
    """Check SSH endpoints, volumes, and syncs in staged passes.

    Four phases:
    1. SSH endpoints → ``ssh_endpoint_statuses`` (observation + interpretation)
    2. Volumes → ``volume_statuses`` (skip volumes on inactive SSH endpoints)
    3. Sync endpoints → statuses (skip endpoints on inactive volumes)
    4. Syncs → ``sync_statuses`` (pure computation)

    When *only_syncs* is given, only those syncs (and the
    volumes/endpoints they reference) are checked.

    Progress callbacks use labels like ``"ssh:localhost"``,
    ``"vol:usb-backup"``, ``"src:photos-local"``, ``"dst:photos-backup"``
    to describe what is being checked.
    """
    re = resolved_endpoints or {}
    progress = _Progress(on_check_start, on_check_end)
    syncs = _selected_syncs(config, only_syncs)
    needed_volumes = _needed_volumes(config, syncs, only_syncs)

    ssh_statuses = _check_ssh_endpoints(config, needed_volumes, syncs, re, progress)
    volume_statuses = _check_volumes(
        config, needed_volumes, ssh_statuses, re, mount_observations or {}, progress
    )
    src_ep_statuses, dst_ep_statuses = _check_sync_endpoints(
        config, syncs, volume_statuses, re, progress
    )
    return PreflightResult(
        ssh_endpoint_statuses=ssh_statuses,
        volume_statuses=volume_statuses,
        source_endpoint_statuses=src_ep_statuses,
        destination_endpoint_statuses=dst_ep_statuses,
        sync_statuses=_sync_statuses(
            config, syncs, src_ep_statuses, dst_ep_statuses, dry_run
        ),
    )


def check_step_count(
    config: Config,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> int:
    """How many progress steps :func:`check_all_syncs` reports.

    One per SSH endpoint, volume, source endpoint and destination endpoint,
    so a progress bar can be sized before the checks start.
    """
    re = resolved_endpoints or {}
    syncs = _selected_syncs(config, only_syncs)
    needed_volumes = _needed_volumes(config, syncs, only_syncs)
    volume_endpoints = {
        *_volumes_by_ssh_endpoint(config, needed_volumes, re),
        *_excluded_endpoints(config, needed_volumes, re),
    }
    return (
        len(volume_endpoints)
        + len(_standalone_endpoints(config, volume_endpoints))
        + len(needed_volumes)
        + len({config.source_endpoint(sc).slug for sc in syncs.values()})
        + len({config.destination_endpoint(sc).slug for sc in syncs.values()})
    )


def _selected_syncs(
    config: Config, only_syncs: list[str] | None
) -> dict[str, SyncConfig]:
    return (
        {s: sc for s, sc in config.syncs.items() if s in only_syncs}
        if only_syncs
        else config.syncs
    )


def _needed_volumes(
    config: Config,
    syncs: dict[str, SyncConfig],
    only_syncs: list[str] | None,
) -> set[str]:
    """Volumes referenced by the selected syncs (all volumes when unfiltered)."""
    return (
        {config.source_endpoint(sc).volume for sc in syncs.values()}
        | {config.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(config.volumes.keys())
    )


def _check_ssh_endpoints(
    config: Config,
    needed_volumes: set[str],
    syncs: dict[str, SyncConfig],
    resolved_endpoints: ResolvedEndpoints,
    progress: _Progress,
) -> dict[str, SshEndpointStatus]:
    """Each endpoint is probed once, through a representative volume.

    Volumes are grouped by the endpoint resolution chose for them, so a volume
    reached through an alternate is probed (and reported) under that
    alternate. A location-excluded volume is never probed: its primary
    endpoint is reported as excluded unless another volume reaches it.
    Endpoints no needed volume uses (bastions, alternates, orphans) are
    probed for reachability only.
    """
    by_endpoint = _volumes_by_ssh_endpoint(config, needed_volumes, resolved_endpoints)
    probed = {
        ssh_slug: progress.track(
            f"ssh:{ssh_slug}",
            partial(
                _check_volume_ssh_endpoint,
                config,
                ssh_slug,
                vol_slugs,
                syncs,
                resolved_endpoints,
            ),
        )
        for ssh_slug, vol_slugs in by_endpoint.items()
    }
    excluded = {
        slug: progress.track(f"ssh:{slug}", partial(_excluded_ssh_status, slug))
        for slug in _excluded_endpoints(config, needed_volumes, resolved_endpoints)
        if slug not in probed
    }
    volume_endpoints = {**probed, **excluded}
    standalone = {
        slug: progress.track(
            f"ssh:{slug}", partial(_check_standalone_endpoint, config, slug)
        )
        for slug in _standalone_endpoints(config, volume_endpoints.keys())
    }
    return {**volume_endpoints, **standalone}


def _standalone_endpoints(
    config: Config, volume_endpoints: Collection[str]
) -> list[str]:
    """SSH endpoints probed for reachability only (sorted)."""
    return sorted(set(config.ssh_endpoints) - set(volume_endpoints))


def _volumes_by_ssh_endpoint(
    config: Config,
    needed_volumes: set[str],
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, list[str]]:
    """Needed, reachable volume slugs grouped by the SSH endpoint resolved for
    them (sorted, stable)."""
    pairs = sorted(
        (_ssh_endpoint_slug(config.volumes[v], resolved_endpoints), v)
        for v in needed_volumes
        if not location_excluded(config.volumes[v], resolved_endpoints)
    )
    return {
        ssh_slug: [v for s, v in pairs if s == ssh_slug]
        for ssh_slug in dict.fromkeys(s for s, _ in pairs)
    }


def _excluded_endpoints(
    config: Config,
    needed_volumes: set[str],
    resolved_endpoints: ResolvedEndpoints,
) -> list[str]:
    """Primary endpoints of the needed volumes excluded by location (sorted)."""
    return sorted(
        {
            _ssh_endpoint_slug(vol, resolved_endpoints)
            for vol in (config.volumes[v] for v in needed_volumes)
            if location_excluded(vol, resolved_endpoints)
        }
    )


def _excluded_ssh_status(slug: str) -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug=slug, diagnostics=SshEndpointDiagnostics(location_excluded=True)
    )


def _volume_ssh_status(
    volume: Volume,
    ssh_statuses: dict[str, SshEndpointStatus],
    resolved_endpoints: ResolvedEndpoints,
) -> SshEndpointStatus:
    """The status of the endpoint *volume* is reached through.

    An excluded volume gets its own excluded status even when another volume
    reached its primary endpoint, since nothing may reach this one.
    """
    slug = _ssh_endpoint_slug(volume, resolved_endpoints)
    return (
        _excluded_ssh_status(slug)
        if location_excluded(volume, resolved_endpoints)
        else ssh_statuses[slug]
    )


def _check_volume_ssh_endpoint(
    config: Config,
    ssh_slug: str,
    vol_slugs: list[str],
    syncs: dict[str, SyncConfig],
    resolved_endpoints: ResolvedEndpoints,
) -> SshEndpointStatus:
    diag = observe_ssh_endpoint(
        config.volumes[vol_slugs[0]],
        resolved_endpoints=resolved_endpoints,
        probe_mount_tools=any(config.volumes[v].mount is not None for v in vol_slugs),
    )
    return SshEndpointStatus.from_diagnostics(
        slug=ssh_slug,
        diagnostics=diag,
        needs=_compute_tool_needs(config, vol_slugs, syncs),
    )


def _check_standalone_endpoint(config: Config, slug: str) -> SshEndpointStatus:
    """Reachability-only probe for an endpoint no needed volume uses."""
    server = enrich_from_ssh_config(config.ssh_endpoints[slug])
    proxy_chain = [
        enrich_from_ssh_config(hop) for hop in resolve_proxy_chain(config, server)
    ]
    return SshEndpointStatus.from_diagnostics(
        slug=slug,
        diagnostics=observe_standalone_endpoint(server, proxy_chain),
    )


def _ssh_endpoint_slug(volume: Volume, resolved_endpoints: ResolvedEndpoints) -> str:
    """The endpoint resolution chose for *volume*, else its primary."""
    match volume:
        case LocalVolume():
            return "localhost"
        case RemoteVolume() if volume.slug in resolved_endpoints:
            return resolved_endpoints[volume.slug].server.slug
        case RemoteVolume():
            return volume.ssh_endpoint


def _endpoints_on(
    config: Config,
    vol_slugs: list[str],
    syncs: dict[str, SyncConfig],
) -> list[SyncEndpoint]:
    return [
        ep
        for sync in syncs.values()
        for ep in (config.source_endpoint(sync), config.destination_endpoint(sync))
        if ep.volume in vol_slugs
    ]


def _compute_tool_needs(
    config: Config,
    vol_slugs: list[str],
    syncs: dict[str, SyncConfig],
) -> SshEndpointToolNeeds:
    endpoints = _endpoints_on(config, vol_slugs, syncs)
    btrfs_volumes = {ep.volume for ep in endpoints if ep.btrfs_snapshots.enabled}
    mount_volumes = {v for v in vol_slugs if config.volumes[v].mount is not None}
    return SshEndpointToolNeeds(
        has_btrfs_endpoints=bool(btrfs_volumes),
        has_snapshot_endpoints=any(ep.snapshot_mode != "none" for ep in endpoints),
        has_mount_volumes=bool(mount_volumes),
        # A mount-managed volume backing a btrfs-snapshot endpoint is on
        # btrfs and wants the udisks btrfs module.
        has_btrfs_mount=bool(mount_volumes & btrfs_volumes),
    )


def _check_volumes(
    config: Config,
    needed_volumes: set[str],
    ssh_statuses: dict[str, SshEndpointStatus],
    resolved_endpoints: ResolvedEndpoints,
    mount_observations: dict[str, MountObservation],
    progress: _Progress,
) -> dict[str, VolumeStatus]:
    """Observe and interpret every needed volume (sorted for stable output)."""
    return {
        slug: progress.track(
            f"vol:{slug}",
            partial(
                _check_volume,
                config.volumes[slug],
                _volume_ssh_status(
                    config.volumes[slug], ssh_statuses, resolved_endpoints
                ),
                resolved_endpoints,
                mount_observations.get(slug),
            ),
        )
        for slug in sorted(needed_volumes)
    }


def _check_volume(
    vol: Volume,
    ssh_status: SshEndpointStatus,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None,
) -> VolumeStatus:
    """Observe one volume unless its (remote) SSH endpoint is inactive."""
    host_tools = ssh_status.diagnostics.host_tools
    observable = ssh_status.active or isinstance(vol, LocalVolume)
    try:
        diagnostics = (
            observe_volume(
                vol,
                host_tools=host_tools,
                mount_tools=ssh_status.diagnostics.mount_tools,
                resolved_endpoints=resolved_endpoints,
                mount_observation=mount_observation,
            )
            if observable and host_tools is not None
            else None
        )
    except SSH_CONNECTION_ERRORS as e:
        return _disconnected_volume_status(vol, ssh_status, e)
    return VolumeStatus.from_diagnostics(
        slug=vol.slug,
        config=vol,
        ssh_endpoint_status=ssh_status,
        diagnostics=diagnostics,
    )


def _disconnected_volume_status(
    vol: Volume, ssh_status: SshEndpointStatus, error: BaseException
) -> VolumeStatus:
    """A remote volume whose host dropped after answering the first probes.

    Every remote command opens a new connection, so a host can go away
    (a NAS falling asleep, Wi-Fi dropping) half-way through preflight.  That
    host is then unreachable for this volume — inactive, with the cause
    recorded — rather than a traceback that stops every other sync too.
    For a local volume the error is not a connection failure: it propagates.
    """
    match vol:
        case RemoteVolume():
            return VolumeStatus.from_diagnostics(
                slug=vol.slug,
                config=vol,
                ssh_endpoint_status=SshEndpointStatus.from_diagnostics(
                    slug=ssh_status.slug, diagnostics=unreachable_diagnostics(error)
                ),
                diagnostics=None,
            )
        case LocalVolume():
            raise error


def _check_sync_endpoints(
    config: Config,
    syncs: dict[str, SyncConfig],
    volume_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints,
    progress: _Progress,
) -> tuple[dict[str, SourceEndpointStatus], dict[str, DestinationEndpointStatus]]:
    src_eps = {
        config.source_endpoint(s).slug: config.source_endpoint(s)
        for s in syncs.values()
    }
    dst_eps = {
        config.destination_endpoint(s).slug: config.destination_endpoint(s)
        for s in syncs.values()
    }

    src = {
        slug: progress.track(
            f"src:{slug}",
            partial(
                _check_source_endpoint,
                ep,
                volume_statuses[ep.volume],
                _endpoint_probe(config, ep, volume_statuses),
                resolved_endpoints,
            ),
        )
        for slug, ep in src_eps.items()
    }
    dst = {
        slug: progress.track(
            f"dst:{slug}",
            partial(
                _check_destination_endpoint,
                ep,
                volume_statuses[ep.volume],
                _endpoint_probe(config, ep, volume_statuses),
                resolved_endpoints,
            ),
        )
        for slug, ep in dst_eps.items()
    }
    return src, dst


def _check_source_endpoint(
    ep: SyncEndpoint,
    volume_status: VolumeStatus,
    probe: _EndpointProbe | None,
    resolved_endpoints: ResolvedEndpoints,
) -> SourceEndpointStatus:
    try:
        diagnostics = (
            observe_source_endpoint(ep, probe.volume, resolved_endpoints)
            if probe is not None
            else None
        )
    except SSH_CONNECTION_ERRORS as e:
        return SourceEndpointStatus.from_diagnostics(
            endpoint=ep,
            volume_status=_disconnected_volume_status(
                volume_status.config, volume_status.ssh_endpoint_status, e
            ),
            diagnostics=None,
        )
    return SourceEndpointStatus.from_diagnostics(
        endpoint=ep, volume_status=volume_status, diagnostics=diagnostics
    )


def _check_destination_endpoint(
    ep: SyncEndpoint,
    volume_status: VolumeStatus,
    probe: _EndpointProbe | None,
    resolved_endpoints: ResolvedEndpoints,
) -> DestinationEndpointStatus:
    try:
        diagnostics = (
            observe_destination_endpoint(
                ep,
                probe.volume,
                probe.capabilities,
                resolved_endpoints,
                host_tools=probe.host_tools,
            )
            if probe is not None
            else None
        )
    except SSH_CONNECTION_ERRORS as e:
        return DestinationEndpointStatus.from_diagnostics(
            endpoint=ep,
            volume_status=_disconnected_volume_status(
                volume_status.config, volume_status.ssh_endpoint_status, e
            ),
            diagnostics=None,
        )
    return DestinationEndpointStatus.from_diagnostics(
        endpoint=ep, volume_status=volume_status, diagnostics=diagnostics
    )


@dataclass(frozen=True)
class _EndpointProbe:
    """What observing a sync endpoint needs from the lower layers."""

    volume: Volume
    capabilities: VolumeCapabilities
    host_tools: HostToolCapabilities


def _endpoint_probe(
    config: Config,
    ep: SyncEndpoint,
    volume_statuses: dict[str, VolumeStatus],
) -> _EndpointProbe | None:
    """Probe inputs for *ep*, or ``None`` when its volume is not active."""
    vol_status = volume_statuses[ep.volume]
    vol = config.volumes[ep.volume]
    host_tools = vol_status.ssh_endpoint_status.diagnostics.host_tools
    caps = (
        vol_status.diagnostics.capabilities
        if vol_status.diagnostics is not None
        else None
    )
    return (
        _EndpointProbe(volume=vol, capabilities=caps, host_tools=host_tools)
        if vol_status.active and caps is not None and host_tools is not None
        else None
    )


def _sync_statuses(
    config: Config,
    syncs: dict[str, SyncConfig],
    src_ep_statuses: dict[str, SourceEndpointStatus],
    dst_ep_statuses: dict[str, DestinationEndpointStatus],
    dry_run: bool,
) -> dict[str, SyncStatus]:
    """Sync-level interpretation — pure computation, no I/O."""
    return {
        slug: SyncStatus.from_diagnostics(
            sync=sync,
            src_endpoint=config.source_endpoint(sync),
            src_ep_status=src_ep_statuses[config.source_endpoint(sync).slug],
            dst_ep_status=dst_ep_statuses[config.destination_endpoint(sync).slug],
            all_syncs=config.syncs,
            dry_run=dry_run,
        )
        for slug, sync in syncs.items()
    }


def check_sync(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> SyncStatus:
    """Thin wrapper around ``check_all_syncs`` for single-sync usage.

    Runs the full pipeline (volume checks, diagnostics, status) for one sync.
    Primarily used in tests to exercise the real code path.
    """
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
    """Kept for use in integration/docker tests that call it directly."""
    re = resolved_endpoints or {}
    ssh_status = SshEndpointStatus.from_diagnostics(
        slug=_ssh_endpoint_slug(volume, re),
        diagnostics=observe_ssh_endpoint(volume, re),
    )
    return _check_volume(volume, ssh_status, re, None)
