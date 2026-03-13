"""Sync check orchestration.

Composes volume checks, endpoint diagnostics, and capabilities
into the two primary entry points: ``check_sync`` and ``check_all_syncs``.

Three-phase check hierarchy (each level feeds the next):

1. **Volumes** — ``VolumeStatus`` (reachability + ``VolumeCapabilities``:
   command availability, filesystem type, mount options)
2. **Sync endpoints** — ``SourceEndpointDiagnostics`` /
   ``DestinationEndpointDiagnostics`` (sentinels, directories,
   symlinks — pure observation, no error interpretation)
3. **Syncs** — ``SyncStatus`` (translates diagnostics + capabilities
   into ``SyncError`` values based on each sync's configuration)
"""

from __future__ import annotations

from typing import Callable

from ..config import (
    Config,
    ResolvedEndpoints,
    SyncConfig,
    SyncEndpoint,
)
from ..fsprotocol import DEVNULL_TARGET
from .endpoint_checks import check_destination_endpoint, check_source_endpoint
from .snapshot_checks import _has_upstream_sync
from .status import (
    DestinationEndpointDiagnostics,
    SourceEndpointDiagnostics,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeStatus,
)
from .volume_checks import check_volume

# ── Top-level orchestration ─────────────────────────────────


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Check volumes and syncs in staged passes.

    Three phases:
    1. Volumes → ``volume_statuses`` (reachability + capabilities)
    2. Sync endpoints → diagnostics (skip endpoints on inactive volumes)
    3. Syncs → ``sync_statuses`` (translates diagnostics into errors)

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

    # Phase 1: Volume reachability + capabilities
    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        volume_statuses[slug] = check_volume(config.volumes[slug], re)
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
        src_diagnostics[slug] = check_source_endpoint(
            ep,
            config.volumes[ep.volume],
            volume_statuses[ep.volume].capabilities,  # type: ignore[arg-type]
            re,
        )
        _track(slug)
    for slug in all_src_eps - active_src_eps.keys():
        _track(slug)

    dst_diagnostics: dict[str, DestinationEndpointDiagnostics] = {}
    for slug, ep in active_dst_eps.items():
        dst_diagnostics[slug] = check_destination_endpoint(
            ep,
            config.volumes[ep.volume],
            volume_statuses[ep.volume].capabilities,  # type: ignore[arg-type]
            re,
        )
        _track(slug)
    for slug in all_dst_eps - active_dst_eps.keys():
        _track(slug)

    # Phase 3: Sync checks — pure computation (no I/O), no progress tracking
    sync_statuses = {
        slug: _build_sync_status(
            sync,
            config,
            volume_statuses[config.source_endpoint(sync).volume],
            volume_statuses[config.destination_endpoint(sync).volume],
            src_diagnostics.get(config.source_endpoint(sync).slug),
            dst_diagnostics.get(config.destination_endpoint(sync).slug),
            config.syncs,
            dry_run,
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


def _build_sync_status(
    sync: SyncConfig,
    config: Config,
    src_status: VolumeStatus,
    dst_status: VolumeStatus,
    src_diag: SourceEndpointDiagnostics | None,
    dst_diag: DestinationEndpointDiagnostics | None,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> SyncStatus:
    """Pure translator: build ``SyncStatus`` from pre-computed data.

    Translates volume capabilities, endpoint diagnostics, and sync
    context into ``SyncError`` values.  Performs no I/O or computation —
    all inputs must be pre-populated.
    """
    if not sync.enabled:
        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_status=src_status,
            destination_status=dst_status,
            errors=[SyncError.DISABLED],
        )

    src_cfg = config.source_endpoint(sync)
    dst_cfg = config.destination_endpoint(sync)

    def _src_errors() -> list[SyncError]:
        if src_status.active:
            assert src_status.capabilities is not None
            assert src_diag is not None
            return _source_errors(
                src_diag, src_status.capabilities, src_cfg, sync, all_syncs, dry_run
            )
        else:
            return [SyncError.SOURCE_UNAVAILABLE]

    def _dst_errors() -> list[SyncError]:
        if dst_status.active:
            assert dst_status.capabilities is not None
            assert dst_diag is not None
            return _destination_errors(dst_diag, dst_status.capabilities, dst_cfg)
        else:
            return [SyncError.DESTINATION_UNAVAILABLE]

    dst_latest = dst_diag.latest.snapshot if dst_diag and dst_diag.latest else None

    return SyncStatus(
        slug=sync.slug,
        config=sync,
        source_status=src_status,
        destination_status=dst_status,
        source_diagnostics=src_diag,
        destination_diagnostics=dst_diag,
        errors=[*_src_errors(), *_dst_errors()],
        destination_latest_snapshot=dst_latest,
    )


# ── Diagnostics → errors translation ──────────────────────


def _source_errors(
    diag: SourceEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Translate source diagnostics + capabilities into SyncErrors."""
    return [
        *([SyncError.SOURCE_SENTINEL_NOT_FOUND] if not diag.sentinel_exists else []),
        *_source_rsync_errors(caps),
        *(
            [
                *(
                    [SyncError.SOURCE_SNAPSHOTS_DIR_NOT_FOUND]
                    if diag.snapshot_dirs is not None and not diag.snapshot_dirs.exists
                    else []
                ),
                *_source_latest_errors(diag, sync, all_syncs, dry_run),
            ]
            if endpoint.snapshot_mode != "none"
            else []
        ),
    ]


def _source_rsync_errors(caps: VolumeCapabilities) -> list[SyncError]:
    """Translate source rsync capability into SyncErrors."""
    match (caps.has_rsync, caps.rsync_version_ok):
        case (False, _):
            return [SyncError.SOURCE_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.SOURCE_RSYNC_TOO_OLD]
        case _:
            return []


def _source_latest_errors(
    diag: SourceEndpointDiagnostics,
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Interpret the source latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncError.SOURCE_LATEST_NOT_FOUND]
    elif latest.raw_target == DEVNULL_TARGET:
        # /dev/null interpretation depends on sync-level context
        if not _has_upstream_sync(sync, all_syncs):
            return [SyncError.SOURCE_LATEST_INVALID]
        elif dry_run:
            return [SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING]
        else:
            return []
    elif latest.target_valid is False:
        return [SyncError.SOURCE_LATEST_INVALID]
    else:
        return []


def _destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncError]:
    """Translate destination diagnostics + capabilities into SyncErrors."""
    return [
        *(
            [SyncError.DESTINATION_SENTINEL_NOT_FOUND]
            if not diag.sentinel_exists
            else []
        ),
        *_destination_rsync_errors(caps),
        *_destination_snapshot_backend_errors(diag, caps, endpoint),
        *(
            [SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE]
            if not diag.endpoint_writable
            else []
        ),
        *(_destination_latest_errors(diag) if endpoint.snapshot_mode != "none" else []),
    ]


def _destination_rsync_errors(caps: VolumeCapabilities) -> list[SyncError]:
    """Translate destination rsync capability into SyncErrors."""
    match (caps.has_rsync, caps.rsync_version_ok):
        case (False, _):
            return [SyncError.DESTINATION_RSYNC_NOT_FOUND]
        case (True, False):
            return [SyncError.DESTINATION_RSYNC_TOO_OLD]
        case _:
            return []


def _destination_snapshot_backend_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
    endpoint: SyncEndpoint,
) -> list[SyncError]:
    """Route to the appropriate snapshot backend error check."""
    if endpoint.btrfs_snapshots.enabled:
        return _btrfs_destination_errors(diag, caps)
    elif endpoint.hard_link_snapshots.enabled:
        return _hardlink_destination_errors(diag, caps)
    else:
        return []


def _btrfs_destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate btrfs-specific diagnostics into SyncErrors."""
    if not caps.has_btrfs:
        return [SyncError.DESTINATION_BTRFS_NOT_FOUND]
    else:
        return [
            *([SyncError.DESTINATION_STAT_NOT_FOUND] if not caps.has_stat else []),
            *(
                [SyncError.DESTINATION_FINDMNT_NOT_FOUND]
                if not caps.has_findmnt
                else []
            ),
            *(_btrfs_stat_errors(diag, caps) if caps.has_stat else []),
        ]


def _btrfs_stat_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate btrfs filesystem/subvolume diagnostics (requires stat)."""
    if not caps.is_btrfs_filesystem:
        return [SyncError.DESTINATION_NOT_BTRFS]
    elif diag.btrfs is None or not diag.btrfs.is_subvolume:
        return [SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME]
    else:
        return [
            *(
                [SyncError.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM]
                if caps.has_findmnt and not caps.btrfs_user_subvol_rm
                else []
            ),
            *_btrfs_staging_errors(diag),
            *_snapshot_dirs_errors(diag),
        ]


def _btrfs_staging_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Translate btrfs staging directory diagnostics."""
    assert diag.btrfs is not None
    if not diag.btrfs.staging_dir_exists:
        return [SyncError.DESTINATION_TMP_NOT_FOUND]
    elif diag.btrfs.staging_dir_writable is False:
        return [SyncError.DESTINATION_STAGING_DIR_NOT_WRITABLE]
    else:
        return []


def _hardlink_destination_errors(
    diag: DestinationEndpointDiagnostics,
    caps: VolumeCapabilities,
) -> list[SyncError]:
    """Translate hard-link-specific diagnostics into SyncErrors."""
    if not caps.has_stat:
        return [SyncError.DESTINATION_STAT_NOT_FOUND]
    else:
        return [
            *(
                [SyncError.DESTINATION_NO_HARDLINK_SUPPORT]
                if not caps.hardlink_supported
                else []
            ),
            *_snapshot_dirs_errors(diag),
        ]


def _snapshot_dirs_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Translate snapshot directory diagnostics into SyncErrors."""
    sd = diag.snapshot_dirs
    if sd is None:
        return []
    elif not sd.exists:
        return [SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND]
    elif sd.writable is False:
        return [SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE]
    else:
        return []


def _destination_latest_errors(
    diag: DestinationEndpointDiagnostics,
) -> list[SyncError]:
    """Interpret the destination latest symlink state."""
    latest = diag.latest
    if latest is None or not latest.exists:
        return [SyncError.DESTINATION_LATEST_NOT_FOUND]
    elif latest.target_valid is False:
        return [SyncError.DESTINATION_LATEST_INVALID]
    else:
        return []
