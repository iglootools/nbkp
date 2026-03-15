"""Sync check orchestration.

Composes volume diagnostics, endpoint diagnostics, and capabilities
into the two primary entry points: ``check_sync`` and ``check_all_syncs``.

Three-phase check hierarchy (each level feeds the next):

1. **Volumes** — ``VolumeDiagnostics`` (observation) →
   ``VolumeStatus`` (interpretation via ``VolumeStatus.from_diagnostics``)
2. **Sync endpoints** — ``SourceEndpointDiagnostics`` /
   ``DestinationEndpointDiagnostics`` (sentinels, directories,
   symlinks — pure observation, no error interpretation)
3. **Syncs** — ``SyncStatus`` (interpretation via
   ``SyncStatus.from_diagnostics``)
"""

from __future__ import annotations

from typing import Callable

from ..config import (
    Config,
    SyncConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..mount.observation import MountObservation
from .endpoint_checks import observe_destination_endpoint, observe_source_endpoint
from .status import (
    DestinationEndpointDiagnostics,
    SourceEndpointDiagnostics,
    SyncStatus,
    VolumeStatus,
)
from .volume_checks import observe_volume

# ── Top-level orchestration ─────────────────────────────────


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Check volumes and syncs in staged passes.

    Three phases:
    1. Volumes → ``volume_statuses`` (observation + interpretation)
    2. Sync endpoints → diagnostics (skip endpoints on inactive volumes)
    3. Syncs → ``sync_statuses`` (interpretation via ``SyncStatus.from_diagnostics``)

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

    # Phase 1: Volume observation + interpretation
    mo = mount_observations or {}
    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        diag = observe_volume(config.volumes[slug], re, mount_observation=mo.get(slug))
        volume_statuses[slug] = VolumeStatus.from_diagnostics(
            slug=slug,
            config=config.volumes[slug],
            diagnostics=diag,
        )
        _track(slug)

    # Phase 2: Sync endpoint diagnostics (skip endpoints on inactive volumes)
    active_src_eps = {
        ep.slug: ep
        for sync in syncs.values()
        if volume_statuses[(ep := config.source_endpoint(sync)).volume].active
    }
    active_dst_eps = {
        ep.slug: ep
        for sync in syncs.values()
        if volume_statuses[(ep := config.destination_endpoint(sync)).volume].active
    }
    all_src_eps = {config.source_endpoint(sync).slug for sync in syncs.values()}
    all_dst_eps = {config.destination_endpoint(sync).slug for sync in syncs.values()}

    src_diagnostics: dict[str, SourceEndpointDiagnostics] = {}
    for slug, ep in active_src_eps.items():
        src_diagnostics[slug] = observe_source_endpoint(
            ep,
            config.volumes[ep.volume],
            volume_statuses[ep.volume].diagnostics.capabilities,  # type: ignore[arg-type]
            re,
        )
        _track(slug)
    for slug in all_src_eps - active_src_eps.keys():
        _track(slug)

    dst_diagnostics: dict[str, DestinationEndpointDiagnostics] = {}
    for slug, ep in active_dst_eps.items():
        dst_diagnostics[slug] = observe_destination_endpoint(
            ep,
            config.volumes[ep.volume],
            volume_statuses[ep.volume].diagnostics.capabilities,  # type: ignore[arg-type]
            re,
        )
        _track(slug)
    for slug in all_dst_eps - active_dst_eps.keys():
        _track(slug)

    # Phase 3: Sync checks — pure computation (no I/O), no progress tracking
    sync_statuses = {
        slug: SyncStatus.from_diagnostics(
            sync=sync,
            src_endpoint=config.source_endpoint(sync),
            dst_endpoint=config.destination_endpoint(sync),
            src_status=volume_statuses[config.source_endpoint(sync).volume],
            dst_status=volume_statuses[config.destination_endpoint(sync).volume],
            src_diag=src_diagnostics.get(config.source_endpoint(sync).slug),
            dst_diag=dst_diagnostics.get(config.destination_endpoint(sync).slug),
            all_syncs=config.syncs,
            dry_run=dry_run,
        )
        for slug, sync in syncs.items()
    }

    return volume_statuses, sync_statuses


# Convenience function used by tests
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
    _, sync_statuses = check_all_syncs(
        config,
        only_syncs=[sync.slug],
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
    )
    return sync_statuses[sync.slug]


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeStatus:
    """Convenience: observe + interpret in one call.

    Kept for use in integration/docker tests that call it directly.
    """
    diag = observe_volume(volume, resolved_endpoints)
    return VolumeStatus.from_diagnostics(
        slug=volume.slug,
        config=volume,
        diagnostics=diag,
    )
