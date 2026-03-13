"""Sync check orchestration.

Composes volume checks, endpoint diagnostics, and capabilities
into the two primary entry points: ``check_sync`` and ``check_all_syncs``.

Four-phase check hierarchy (each level caches results for the next):

1. **Volume reachability** — ``VolumeStatus`` (sentinel + SSH)
2. **Volume capabilities** — ``VolumeCapabilities`` (command availability,
   filesystem type, mount options)
3. **Sync endpoints** — ``SourceEndpointDiagnostics`` /
   ``DestinationEndpointDiagnostics`` (sentinels, directories,
   symlinks — pure observation, no error interpretation)
4. **Syncs** — ``SyncStatus`` (translates diagnostics + capabilities
   into ``SyncReason`` values based on each sync's configuration)
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
from ..conventions import DEVNULL_TARGET
from .endpoint_checks import check_destination_endpoint, check_source_endpoint
from .snapshot_checks import _has_upstream_sync
from .status import (
    DestinationEndpointDiagnostics,
    SourceEndpointDiagnostics,
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
    source_diagnostics_cache: dict[str, SourceEndpointDiagnostics] | None = None,
    destination_diagnostics_cache: (
        dict[str, DestinationEndpointDiagnostics] | None
    ) = None,
) -> SyncStatus:
    """Check if a sync is active, accumulating all failure reasons.

    When caches are provided, pre-computed results are reused.
    When ``None``, checks run inline (standalone usage).
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
    src_diag: SourceEndpointDiagnostics | None = None
    dst_diag: DestinationEndpointDiagnostics | None = None

    # Volume availability
    if not src_status.active:
        reasons.append(SyncReason.SOURCE_UNAVAILABLE)

    if not dst_status.active:
        reasons.append(SyncReason.DESTINATION_UNAVAILABLE)

    # Source checks (only if source volume is active)
    if src_status.active:
        src_caps = _get_or_compute_capabilities(
            src_status.slug, src_status, re, volume_capabilities
        )
        src_diag = _get_or_check_source(
            src_cfg, src_vol, src_caps, re, source_diagnostics_cache
        )
        reasons.extend(
            _source_reasons(src_diag, src_caps, src_cfg, sync, syncs, dry_run)
        )

    # Destination checks (only if dest volume is active)
    if dst_status.active:
        dst_caps = _get_or_compute_capabilities(
            dst_status.slug, dst_status, re, volume_capabilities
        )
        dst_diag = _get_or_check_destination(
            dst_cfg, dst_vol, dst_caps, re, destination_diagnostics_cache
        )
        reasons.extend(_destination_reasons(dst_diag, dst_caps, dst_cfg))

    dst_latest = dst_diag.latest.snapshot_name if dst_diag and dst_diag.latest else None

    return SyncStatus(
        slug=sync.slug,
        config=sync,
        source_status=src_status,
        destination_status=dst_status,
        source_diagnostics=src_diag,
        destination_diagnostics=dst_diag,
        reasons=reasons,
        destination_latest_target=dst_latest,
    )


# ── Diagnostics → reasons translation ──────────────────────


def _source_reasons(
    diag: SourceEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncReason]:
    """Translate source diagnostics + capabilities into SyncReasons."""
    reasons: list[SyncReason] = []

    if not diag.sentinel_exists:
        reasons.append(SyncReason.SOURCE_SENTINEL_NOT_FOUND)

    if not caps.has_rsync:
        reasons.append(SyncReason.SOURCE_RSYNC_NOT_FOUND)
    elif not caps.rsync_version_ok:
        reasons.append(SyncReason.SOURCE_RSYNC_TOO_OLD)

    if endpoint.snapshot_mode != "none":
        if diag.snapshot_dirs is not None and not diag.snapshot_dirs.exists:
            reasons.append(SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND)
        reasons.extend(_source_latest_reasons(diag, sync, all_syncs, dry_run))

    return reasons


def _source_latest_reasons(
    diag: SourceEndpointDiagnostics,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncReason]:
    """Interpret the source latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncReason.SOURCE_LATEST_NOT_FOUND]

    if latest.raw_target == DEVNULL_TARGET:
        # /dev/null interpretation depends on sync-level context
        if not _has_upstream_sync(sync, all_syncs):
            return [SyncReason.SOURCE_LATEST_INVALID]
        elif dry_run:
            return [SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING]
        return []

    if latest.target_valid is False:
        return [SyncReason.SOURCE_LATEST_INVALID]

    return []


def _destination_reasons(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncReason]:
    """Translate destination diagnostics + capabilities into SyncReasons."""
    reasons: list[SyncReason] = []

    if not diag.sentinel_exists:
        reasons.append(SyncReason.DESTINATION_SENTINEL_NOT_FOUND)

    if not caps.has_rsync:
        reasons.append(SyncReason.DESTINATION_RSYNC_NOT_FOUND)
    elif not caps.rsync_version_ok:
        reasons.append(SyncReason.DESTINATION_RSYNC_TOO_OLD)

    if endpoint.btrfs_snapshots.enabled:
        reasons.extend(_btrfs_destination_reasons(diag, caps))
    elif endpoint.hard_link_snapshots.enabled:
        reasons.extend(_hardlink_destination_reasons(diag, caps))

    if not diag.endpoint_writable:
        reasons.append(SyncReason.DESTINATION_ENDPOINT_NOT_WRITABLE)

    if endpoint.snapshot_mode != "none":
        reasons.extend(_destination_latest_reasons(diag))

    return reasons


def _btrfs_destination_reasons(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncReason]:
    """Translate btrfs-specific diagnostics into SyncReasons."""
    reasons: list[SyncReason] = []

    if not caps.has_btrfs:
        reasons.append(SyncReason.DESTINATION_BTRFS_NOT_FOUND)
        return reasons

    if not caps.has_stat:
        reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
    if not caps.has_findmnt:
        reasons.append(SyncReason.DESTINATION_FINDMNT_NOT_FOUND)

    if caps.has_stat:
        if not caps.is_btrfs_filesystem:
            reasons.append(SyncReason.DESTINATION_NOT_BTRFS)
        elif diag.btrfs is None or not diag.btrfs.is_subvolume:
            reasons.append(SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME)
        else:
            if caps.has_findmnt and not caps.btrfs_user_subvol_rm:
                reasons.append(SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM)
            if not diag.btrfs.staging_dir_exists:
                reasons.append(SyncReason.DESTINATION_TMP_NOT_FOUND)
            elif diag.btrfs.staging_dir_writable is False:
                reasons.append(SyncReason.DESTINATION_STAGING_DIR_NOT_WRITABLE)
            reasons.extend(_snapshot_dirs_reasons(diag))

    return reasons


def _hardlink_destination_reasons(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncReason]:
    """Translate hard-link-specific diagnostics into SyncReasons."""
    reasons: list[SyncReason] = []

    if not caps.has_stat:
        reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
        return reasons

    if not caps.hardlink_supported:
        reasons.append(SyncReason.DESTINATION_NO_HARDLINK_SUPPORT)
    reasons.extend(_snapshot_dirs_reasons(diag))

    return reasons


def _snapshot_dirs_reasons(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncReason]:
    """Translate snapshot directory diagnostics into SyncReasons."""
    sd = diag.snapshot_dirs
    if sd is None:
        return []
    if not sd.exists:
        return [SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND]
    if sd.writable is False:
        return [SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE]
    return []


def _destination_latest_reasons(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncReason]:
    """Interpret the destination latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncReason.DESTINATION_LATEST_NOT_FOUND]
    if latest.target_valid is False:
        return [SyncReason.DESTINATION_LATEST_INVALID]
    return []


# ── Cache helpers ───────────────────────────────────────────


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


def _get_or_check_source(
    src_cfg: SyncEndpoint,
    src_vol: Volume,
    caps: VolumeCapabilities,
    re: ResolvedEndpoints,
    cache: dict[str, SourceEndpointDiagnostics] | None,
) -> SourceEndpointDiagnostics:
    """Return cached source diagnostics or compute inline."""
    if cache and src_cfg.slug in cache:
        return cache[src_cfg.slug]
    return check_source_endpoint(src_cfg, src_vol, caps, re)


def _get_or_check_destination(
    dst_cfg: SyncEndpoint,
    dst_vol: Volume,
    caps: VolumeCapabilities,
    re: ResolvedEndpoints,
    cache: dict[str, DestinationEndpointDiagnostics] | None,
) -> DestinationEndpointDiagnostics:
    """Return cached destination diagnostics or compute inline."""
    if cache and dst_cfg.slug in cache:
        return cache[dst_cfg.slug]
    return check_destination_endpoint(dst_cfg, dst_vol, caps, re)


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
    3. Sync endpoints → diagnostics caches (skip inactive volumes)
    4. Syncs → ``sync_statuses`` (translates diagnostics into reasons)

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
            volume_statuses[slug] = VolumeStatus(
                slug=vs.slug,
                config=vs.config,
                reasons=vs.reasons,
                capabilities=caps,
            )

    # Phase 3: Sync endpoint diagnostics (skip endpoints on inactive volumes)
    src_diag_cache: dict[str, SourceEndpointDiagnostics] = {}
    dst_diag_cache: dict[str, DestinationEndpointDiagnostics] = {}
    for sync in syncs.values():
        src_cfg = config.source_endpoint(sync)
        dst_cfg = config.destination_endpoint(sync)

        if (
            src_cfg.slug not in src_diag_cache
            and volume_statuses[src_cfg.volume].active
        ):
            src_vol = config.volumes[src_cfg.volume]
            caps = volume_capabilities[src_cfg.volume]
            src_diag_cache[src_cfg.slug] = check_source_endpoint(
                src_cfg, src_vol, caps, re
            )

        if (
            dst_cfg.slug not in dst_diag_cache
            and volume_statuses[dst_cfg.volume].active
        ):
            dst_vol = config.volumes[dst_cfg.volume]
            caps = volume_capabilities[dst_cfg.volume]
            dst_diag_cache[dst_cfg.slug] = check_destination_endpoint(
                dst_cfg, dst_vol, caps, re
            )

    # Phase 4: Sync checks (translates diagnostics + capabilities into reasons)
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
            source_diagnostics_cache=src_diag_cache,
            destination_diagnostics_cache=dst_diag_cache,
        )
        if on_progress:
            on_progress(slug)

    return volume_statuses, sync_statuses
