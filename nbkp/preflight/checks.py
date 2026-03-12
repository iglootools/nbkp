"""Sync check orchestration.

Composes queries, volume checks, endpoint checks, and snapshot checks
into the two primary entry points: ``check_sync`` and ``check_all_syncs``.

Four-phase check hierarchy (each level caches results for the next):

1. **Volume reachability** — ``VolumeStatus`` (sentinel + SSH)
2. **Volume capabilities** — ``VolumeCapabilities`` (command availability,
   filesystem type, mount options)
3. **Sync endpoints** — ``SyncEndpointStatus`` (sentinels, directories,
   snapshot readiness, latest symlink)
4. **Syncs** — ``SyncStatus`` (source latest interpretation, upstream deps)
"""

from __future__ import annotations

from typing import Callable

from ..config import (
    Config,
    ResolvedEndpoints,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from .endpoint_checks import check_destination_endpoint, check_source_endpoint
from .snapshot_checks import _has_upstream_sync
from .status import (
    SyncEndpointRole,
    SyncEndpointStatus,
    SyncReason,
    SyncStatus,
    VolumeCapabilities,
    VolumeStatus,
)
from .volume_checks import check_volume, check_volume_capabilities


def check_sync(
    sync: SyncConfig,
    config: Config,
    volume_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints | None = None,
    all_syncs: dict[str, SyncConfig] | None = None,
    dry_run: bool = False,
    volume_capabilities: dict[str, VolumeCapabilities] | None = None,
    endpoint_statuses: (
        dict[tuple[str, SyncEndpointRole], SyncEndpointStatus] | None
    ) = None,
) -> SyncStatus:
    """Check if a sync is active, accumulating all failure reasons.

    When *volume_capabilities* and *endpoint_statuses* are provided,
    pre-computed results are reused.  When ``None``, checks run inline
    (standalone usage).
    """
    re = resolved_endpoints or {}
    syncs = all_syncs if all_syncs is not None else config.syncs
    src_cfg = config.source_endpoint(sync)
    dst_cfg = config.destination_endpoint(sync)
    src_vol = config.volumes[src_cfg.volume]
    dst_vol = config.volumes[dst_cfg.volume]

    src_status = volume_statuses[src_cfg.volume]
    dst_status = volume_statuses[dst_cfg.volume]

    if not sync.enabled:
        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            reasons=[SyncReason.DISABLED],
        )

    reasons: list[SyncReason] = []
    src_ep_status: SyncEndpointStatus | None = None
    dst_ep_status: SyncEndpointStatus | None = None

    # Volume availability
    if not src_status.active:
        reasons.append(SyncReason.SOURCE_UNAVAILABLE)

    if not dst_status.active:
        reasons.append(SyncReason.DESTINATION_UNAVAILABLE)

    # Source endpoint checks (only if source volume is active)
    if src_status.active:
        src_ep_status = _get_or_check_source_endpoint(
            src_cfg.slug,
            src_cfg,
            src_vol,
            src_status,
            re,
            endpoint_statuses,
            volume_capabilities,
        )
        reasons.extend(src_ep_status.reasons)

        # Source latest /dev/null interpretation (sync-scoped)
        if src_cfg.snapshot_mode != "none":
            reasons.extend(
                _interpret_source_latest(
                    sync,
                    src_ep_status,
                    syncs,
                    dry_run,
                )
            )

    # Destination endpoint checks (only if dest volume is active)
    if dst_status.active:
        dst_ep_status = _get_or_check_destination_endpoint(
            dst_cfg.slug,
            dst_cfg,
            dst_vol,
            dst_status,
            re,
            endpoint_statuses,
            volume_capabilities,
        )
        reasons.extend(dst_ep_status.reasons)

    dst_latest_target = dst_ep_status.latest_target if dst_ep_status else None

    return SyncStatus(
        slug=sync.slug,
        config=sync,
        source_status=src_status,
        destination_status=dst_status,
        source_endpoint_status=src_ep_status,
        destination_endpoint_status=dst_ep_status,
        reasons=reasons,
        destination_latest_target=dst_latest_target,
    )


# ── Helpers ─────────────────────────────────────────────────


def _get_or_compute_capabilities(
    volume_slug: str,
    vol_status: VolumeStatus,
    re: ResolvedEndpoints,
    volume_capabilities: dict[str, VolumeCapabilities] | None,
) -> VolumeCapabilities:
    """Return cached capabilities or compute them inline."""
    if volume_capabilities and volume_slug in volume_capabilities:
        return volume_capabilities[volume_slug]
    if vol_status.capabilities is not None:
        return vol_status.capabilities
    return check_volume_capabilities(vol_status.config, re)


def _get_or_check_source_endpoint(
    endpoint_slug: str,
    src_cfg: SyncEndpoint,
    src_vol: Volume,
    src_status: VolumeStatus,
    re: ResolvedEndpoints,
    endpoint_statuses: (dict[tuple[str, SyncEndpointRole], SyncEndpointStatus] | None),
    volume_capabilities: dict[str, VolumeCapabilities] | None,
) -> SyncEndpointStatus:
    """Return cached source endpoint status or compute inline."""
    key: tuple[str, SyncEndpointRole] = (endpoint_slug, "source")
    if endpoint_statuses and key in endpoint_statuses:
        return endpoint_statuses[key]
    caps = _get_or_compute_capabilities(
        src_status.slug, src_status, re, volume_capabilities
    )
    return check_source_endpoint(src_cfg, src_vol, caps, re)


def _get_or_check_destination_endpoint(
    endpoint_slug: str,
    dst_cfg: SyncEndpoint,
    dst_vol: Volume,
    dst_status: VolumeStatus,
    re: ResolvedEndpoints,
    endpoint_statuses: (dict[tuple[str, SyncEndpointRole], SyncEndpointStatus] | None),
    volume_capabilities: dict[str, VolumeCapabilities] | None,
) -> SyncEndpointStatus:
    """Return cached destination endpoint status or compute inline."""
    key: tuple[str, SyncEndpointRole] = (endpoint_slug, "destination")
    if endpoint_statuses and key in endpoint_statuses:
        return endpoint_statuses[key]
    caps = _get_or_compute_capabilities(
        dst_status.slug, dst_status, re, volume_capabilities
    )
    return check_destination_endpoint(dst_cfg, dst_vol, caps, re)


def _interpret_source_latest(
    sync: SyncConfig,
    src_ep_status: SyncEndpointStatus,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncReason]:
    """Interpret /dev/null in source latest (sync-scoped logic).

    The endpoint-level check reads the symlink and validates it exists
    and points to a valid target.  But ``/dev/null`` acceptance depends
    on whether an upstream sync exists and whether we're in dry-run mode.
    This is the only check that stays per-sync.

    If the endpoint already flagged SOURCE_LATEST_NOT_FOUND or
    SOURCE_LATEST_INVALID, skip — those are already in the reasons.
    """
    # If endpoint-level checks already found issues, don't add more
    endpoint_has_latest_issue = any(
        r in (SyncReason.SOURCE_LATEST_NOT_FOUND, SyncReason.SOURCE_LATEST_INVALID)
        for r in src_ep_status.reasons
    )
    if endpoint_has_latest_issue:
        return []

    # If latest_target is not None, endpoint validated it points to a real dir
    if src_ep_status.latest_target is not None:
        return []

    # latest_target is None and no endpoint error → /dev/null case
    # (endpoint check accepts /dev/null without adding reasons)
    if not _has_upstream_sync(sync, all_syncs):
        return [SyncReason.SOURCE_LATEST_INVALID]
    elif dry_run:
        return [SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING]
    return []


# ── Top-level orchestration ─────────────────────────────────


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Check volumes and syncs with multi-level caching.

    Four phases:
    1. Volume reachability → ``volume_statuses``
    2. Volume capabilities → ``volume_capabilities`` (skip inactive)
    3. Sync endpoints → ``endpoint_statuses`` (skip inactive volumes)
    4. Syncs → ``sync_statuses`` (uses all pre-computed caches)

    When *only_syncs* is given, only those syncs (and the
    volumes/endpoints they reference) are checked.
    """
    re = resolved_endpoints or {}
    syncs = (
        {s: sc for s, sc in config.syncs.items() if s in only_syncs}
        if only_syncs
        else config.syncs
    )

    needed_volumes: set[str] = (
        {config.source_endpoint(sc).volume for sc in syncs.values()}
        | {config.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(config.volumes.keys())
    )

    # Phase 1: Volume reachability
    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        volume = config.volumes[slug]
        volume_statuses[slug] = check_volume(volume, re)
        if on_progress:
            on_progress(slug)

    # Phase 2: Volume capabilities (skip inactive volumes)
    volume_capabilities: dict[str, VolumeCapabilities] = {}
    for slug, vs in volume_statuses.items():
        if vs.active:
            caps = check_volume_capabilities(vs.config, re)
            volume_capabilities[slug] = caps
            # Attach capabilities to the volume status
            volume_statuses[slug] = VolumeStatus(
                slug=vs.slug,
                config=vs.config,
                reasons=vs.reasons,
                capabilities=caps,
            )

    # Phase 3: Sync endpoint checks (skip endpoints on inactive volumes)
    endpoint_statuses: dict[tuple[str, SyncEndpointRole], SyncEndpointStatus] = {}
    for sync in syncs.values():
        src_cfg = config.source_endpoint(sync)
        dst_cfg = config.destination_endpoint(sync)

        src_key: tuple[str, SyncEndpointRole] = (src_cfg.slug, "source")
        if src_key not in endpoint_statuses and volume_statuses[src_cfg.volume].active:
            src_vol = config.volumes[src_cfg.volume]
            caps = volume_capabilities[src_cfg.volume]
            endpoint_statuses[src_key] = check_source_endpoint(
                src_cfg, src_vol, caps, re
            )

        dst_key: tuple[str, SyncEndpointRole] = (dst_cfg.slug, "destination")
        if dst_key not in endpoint_statuses and volume_statuses[dst_cfg.volume].active:
            dst_vol = config.volumes[dst_cfg.volume]
            caps = volume_capabilities[dst_cfg.volume]
            endpoint_statuses[dst_key] = check_destination_endpoint(
                dst_cfg, dst_vol, caps, re
            )

    # Phase 4: Sync checks (uses all pre-computed caches)
    sync_statuses: dict[str, SyncStatus] = {}
    for slug, sync in syncs.items():
        sync_statuses[slug] = check_sync(
            sync,
            config,
            volume_statuses,
            re,
            config.syncs,
            dry_run=dry_run,
            volume_capabilities=volume_capabilities,
            endpoint_statuses=endpoint_statuses,
        )
        if on_progress:
            on_progress(slug)

    return volume_statuses, sync_statuses
