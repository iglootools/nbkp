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

from __future__ import annotations

from typing import Callable

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..mount.observation import MountObservation
from .endpoint_checks import observe_destination_endpoint, observe_source_endpoint
from .status import (
    DestinationEndpointDiagnostics,
    DestinationEndpointStatus,
    PreflightResult,
    SourceEndpointDiagnostics,
    SourceEndpointStatus,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SyncStatus,
    VolumeStatus,
)
from .volume_checks import observe_ssh_endpoint, observe_volume

# ── Top-level orchestration ─────────────────────────────────


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
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
    """
    re = resolved_endpoints or {}
    syncs = (
        {s: sc for s, sc in config.syncs.items() if s in only_syncs}
        if only_syncs
        else config.syncs
    )

    needed_volumes = (
        {config.source_endpoint(sc).volume for sc in syncs.values()}
        | {config.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(config.volumes.keys())
    )

    def _track(slug: str) -> None:
        if on_progress:
            on_progress(slug)

    # Phase 1: SSH endpoint observation + interpretation
    ssh_statuses = _check_ssh_endpoints(config, needed_volumes, syncs, re)
    for slug in ssh_statuses:
        _track(slug)

    # Phase 2: Volume observation + interpretation
    mo = mount_observations or {}
    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        vol = config.volumes[slug]
        ssh_slug = _ssh_endpoint_slug(vol)
        ssh_status = ssh_statuses[ssh_slug]
        if ssh_status.active:
            diag = observe_volume(
                vol,
                host_tools=ssh_status.diagnostics.host_tools,  # type: ignore[arg-type]
                mount_tools=ssh_status.diagnostics.mount_tools,
                resolved_endpoints=re,
                mount_observation=mo.get(slug),
            )
        else:
            diag = None
        volume_statuses[slug] = VolumeStatus.from_diagnostics(
            slug=slug,
            config=vol,
            ssh_endpoint_status=ssh_status,
            diagnostics=diag,
        )
        _track(slug)

    # Phase 3: Sync endpoint diagnostics + status
    #   Collect unique endpoints, observe active ones, create statuses.
    src_ep_statuses, dst_ep_statuses = _check_sync_endpoints(
        config, syncs, volume_statuses, ssh_statuses, re, _track
    )

    # Phase 4: Sync checks — pure computation (no I/O)
    sync_statuses = {
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

    return PreflightResult(
        ssh_endpoint_statuses=ssh_statuses,
        volume_statuses=volume_statuses,
        sync_statuses=sync_statuses,
    )


# ── Phase 1: SSH endpoints ────────────────────────────────


def _check_ssh_endpoints(
    config: Config,
    needed_volumes: set[str],
    syncs: dict[str, SyncConfig],
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, SshEndpointStatus]:
    """Observe and interpret SSH endpoint statuses.

    Groups volumes by their SSH endpoint, picks a representative
    volume for dispatching commands, computes tool needs from config,
    and creates ``SshEndpointStatus`` for each unique endpoint.
    """
    # Group volumes by SSH endpoint slug
    ssh_to_volumes: dict[str, list[str]] = {}
    for v_slug in needed_volumes:
        vol = config.volumes[v_slug]
        ssh_slug = _ssh_endpoint_slug(vol)
        ssh_to_volumes.setdefault(ssh_slug, []).append(v_slug)

    ssh_statuses: dict[str, SshEndpointStatus] = {}
    for ssh_slug, vol_slugs in ssh_to_volumes.items():
        # Pick a representative volume for dispatching commands
        representative_vol = config.volumes[vol_slugs[0]]
        needs = _compute_tool_needs(config, vol_slugs, syncs)
        probe_mount = any(
            getattr(config.volumes[vs], "mount", None) is not None for vs in vol_slugs
        )
        diag = observe_ssh_endpoint(
            representative_vol,
            resolved_endpoints=resolved_endpoints,
            probe_mount_tools=probe_mount,
        )
        ssh_statuses[ssh_slug] = SshEndpointStatus.from_diagnostics(
            slug=ssh_slug,
            diagnostics=diag,
            needs=needs,
        )

    return ssh_statuses


def _ssh_endpoint_slug(volume: Volume) -> str:
    """Return the SSH endpoint slug for a volume.

    Local volumes use the implicit ``"localhost"`` endpoint.
    Remote volumes use their SSH endpoint slug.
    """
    match volume:
        case LocalVolume():
            return "localhost"
        case RemoteVolume():
            return volume.ssh_endpoint


def _compute_tool_needs(
    config: Config,
    vol_slugs: list[str],
    syncs: dict[str, SyncConfig],
) -> SshEndpointToolNeeds:
    """Compute what tools are required on an SSH endpoint.

    Scans volumes and sync endpoints on the given volumes to determine
    which host-level tools are needed.
    """
    has_btrfs_endpoints = False
    has_snapshot_endpoints = False
    mount_systemd = False
    mount_direct = False
    has_encryption = False

    vol_set = set(vol_slugs)

    for sync in syncs.values():
        for ep_getter in (config.source_endpoint, config.destination_endpoint):
            ep = ep_getter(sync)
            if ep.volume in vol_set:
                if ep.btrfs_snapshots.enabled:
                    has_btrfs_endpoints = True
                    has_snapshot_endpoints = True
                elif ep.hard_link_snapshots.enabled:
                    has_snapshot_endpoints = True

    for vs in vol_slugs:
        vol = config.volumes[vs]
        mount_config = getattr(vol, "mount", None)
        if mount_config is not None:
            if mount_config.encryption is not None:
                has_encryption = True
            match mount_config.strategy:
                case "systemd":
                    mount_systemd = True
                case "direct":
                    mount_direct = True
                case "auto":
                    # Auto could resolve to either — both tools may be needed
                    mount_systemd = True
                    mount_direct = True

    return SshEndpointToolNeeds(
        has_btrfs_endpoints=has_btrfs_endpoints,
        has_snapshot_endpoints=has_snapshot_endpoints,
        mount_systemd=mount_systemd,
        mount_direct=mount_direct,
        has_encryption=has_encryption,
    )


# ── Phase 3: Sync endpoints ──────────────────────────────


def _check_sync_endpoints(
    config: Config,
    syncs: dict[str, SyncConfig],
    volume_statuses: dict[str, VolumeStatus],
    ssh_statuses: dict[str, SshEndpointStatus],
    resolved_endpoints: ResolvedEndpoints,
    track: Callable[[str], None],
) -> tuple[dict[str, SourceEndpointStatus], dict[str, DestinationEndpointStatus]]:
    """Observe and interpret sync endpoint statuses."""
    # Collect unique endpoints
    src_eps = {
        config.source_endpoint(sync).slug: config.source_endpoint(sync)
        for sync in syncs.values()
    }
    dst_eps = {
        config.destination_endpoint(sync).slug: config.destination_endpoint(sync)
        for sync in syncs.values()
    }

    # Source endpoints
    src_ep_statuses: dict[str, SourceEndpointStatus] = {}
    for slug, ep in src_eps.items():
        vol_status = volume_statuses[ep.volume]
        if vol_status.active:
            vol = config.volumes[ep.volume]
            ssh_slug = _ssh_endpoint_slug(vol)
            host_tools = ssh_statuses[ssh_slug].diagnostics.host_tools
            assert host_tools is not None  # active SSH → tools were probed
            src_diag: SourceEndpointDiagnostics | None = observe_source_endpoint(
                ep,
                vol,
                vol_status.diagnostics.capabilities,  # type: ignore[union-attr]
                resolved_endpoints,
                host_tools=host_tools,
            )
        else:
            src_diag = None
        src_ep_statuses[slug] = SourceEndpointStatus.from_diagnostics(
            endpoint=ep,
            volume_status=vol_status,
            diagnostics=src_diag,
        )
        track(slug)

    # Destination endpoints
    dst_ep_statuses: dict[str, DestinationEndpointStatus] = {}
    for slug, ep in dst_eps.items():
        vol_status = volume_statuses[ep.volume]
        if vol_status.active:
            vol = config.volumes[ep.volume]
            ssh_slug = _ssh_endpoint_slug(vol)
            host_tools = ssh_statuses[ssh_slug].diagnostics.host_tools
            assert host_tools is not None  # active SSH → tools were probed
            dst_diag: DestinationEndpointDiagnostics | None = (
                observe_destination_endpoint(
                    ep,
                    vol,
                    vol_status.diagnostics.capabilities,  # type: ignore[union-attr]
                    resolved_endpoints,
                    host_tools=host_tools,
                )
            )
        else:
            dst_diag = None
        dst_ep_statuses[slug] = DestinationEndpointStatus.from_diagnostics(
            endpoint=ep,
            volume_status=vol_status,
            diagnostics=dst_diag,
        )
        track(slug)

    return src_ep_statuses, dst_ep_statuses


# ── Convenience functions ─────────────────────────────────


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
    """Convenience: observe + interpret in one call.

    Creates an implicit SSH endpoint status and runs volume observation.
    Kept for use in integration/docker tests that call it directly.
    """
    re = resolved_endpoints or {}
    ssh_diag = observe_ssh_endpoint(volume, re)
    ssh_status = SshEndpointStatus.from_diagnostics(
        slug=_ssh_endpoint_slug(volume),
        diagnostics=ssh_diag,
    )
    if ssh_status.active:
        diag = observe_volume(
            volume,
            host_tools=ssh_diag.host_tools,  # type: ignore[arg-type]
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=re,
        )
    else:
        diag = None
    return VolumeStatus.from_diagnostics(
        slug=volume.slug,
        config=volume,
        ssh_endpoint_status=ssh_status,
        diagnostics=diag,
    )
