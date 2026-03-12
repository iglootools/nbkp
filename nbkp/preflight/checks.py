"""Sync check orchestration.

Composes queries, volume checks, and snapshot checks into
the two primary entry points: ``check_sync`` and ``check_all_syncs``.
"""

from __future__ import annotations

from typing import Callable

from ..config import (
    Config,
    ResolvedEndpoints,
    SyncConfig,
)
from ..conventions import DESTINATION_SENTINEL, SNAPSHOTS_DIR, SOURCE_SENTINEL
from .queries import (
    _check_command_available,
    _check_directory_exists,
    _check_directory_writable,
    _check_endpoint_sentinel,
    _check_rsync_version,
    _resolve_endpoint,
)
from .snapshot_checks import (
    _check_btrfs_dest,
    _check_hard_link_dest,
    _check_latest_symlink,
    _check_source_latest,
)
from .status import SyncReason, SyncStatus, VolumeStatus
from .volume_checks import check_volume


def check_sync(
    sync: SyncConfig,
    config: Config,
    volume_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints | None = None,
    all_syncs: dict[str, SyncConfig] | None = None,
    dry_run: bool = False,
) -> SyncStatus:
    """Check if a sync is active, accumulating all failure reasons."""
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
    dst_latest_target: str | None = None

    # Volume availability
    if not src_status.active:
        reasons.append(SyncReason.SOURCE_UNAVAILABLE)

    if not dst_status.active:
        reasons.append(SyncReason.DESTINATION_UNAVAILABLE)

    # Source checks (only if source volume is active)
    if src_status.active:
        if not _check_endpoint_sentinel(
            src_vol,
            src_cfg.subdir,
            SOURCE_SENTINEL,
            re,
        ):
            reasons.append(SyncReason.SOURCE_SENTINEL_NOT_FOUND)
        if not _check_command_available(src_vol, "rsync", re):
            reasons.append(SyncReason.SOURCE_RSYNC_NOT_FOUND)
        elif not _check_rsync_version(src_vol, re):
            reasons.append(SyncReason.SOURCE_RSYNC_TOO_OLD)
        if src_cfg.snapshot_mode != "none":
            src_ep = _resolve_endpoint(src_vol, src_cfg.subdir)
            reasons.extend(
                _check_source_latest(sync, src_vol, src_ep, syncs, re, dry_run=dry_run)
            )
            if not _check_directory_exists(src_vol, f"{src_ep}/{SNAPSHOTS_DIR}", re):
                reasons.append(SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND)

    # Destination checks (only if dest volume is active)
    if dst_status.active:
        if not _check_endpoint_sentinel(
            dst_vol,
            dst_cfg.subdir,
            DESTINATION_SENTINEL,
            re,
        ):
            reasons.append(SyncReason.DESTINATION_SENTINEL_NOT_FOUND)
        if not _check_command_available(dst_vol, "rsync", re):
            reasons.append(SyncReason.DESTINATION_RSYNC_NOT_FOUND)
        elif not _check_rsync_version(dst_vol, re):
            reasons.append(SyncReason.DESTINATION_RSYNC_TOO_OLD)
        if dst_cfg.btrfs_snapshots.enabled:
            if not _check_command_available(dst_vol, "btrfs", re):
                reasons.append(SyncReason.DESTINATION_BTRFS_NOT_FOUND)
            else:
                has_stat = _check_command_available(dst_vol, "stat", re)
                has_findmnt = _check_command_available(dst_vol, "findmnt", re)

                if not has_stat:
                    reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
                if not has_findmnt:
                    reasons.append(SyncReason.DESTINATION_FINDMNT_NOT_FOUND)

                if has_stat:
                    reasons.extend(
                        _check_btrfs_dest(
                            dst_vol,
                            dst_cfg.subdir,
                            has_findmnt,
                            re,
                        )
                    )
        elif dst_cfg.hard_link_snapshots.enabled:
            has_stat = _check_command_available(dst_vol, "stat", re)
            if not has_stat:
                reasons.append(SyncReason.DESTINATION_STAT_NOT_FOUND)
            else:
                reasons.extend(
                    _check_hard_link_dest(
                        dst_vol,
                        dst_cfg.subdir,
                        re,
                    )
                )

        # Destination endpoint writability
        dst_ep = _resolve_endpoint(dst_vol, dst_cfg.subdir)
        if not _check_directory_writable(dst_vol, dst_ep, re):
            reasons.append(SyncReason.DESTINATION_ENDPOINT_NOT_WRITABLE)

        # Destination latest symlink check (snapshot modes)
        if dst_cfg.snapshot_mode != "none":
            dst_ep = _resolve_endpoint(dst_vol, dst_cfg.subdir)
            dst_latest_target, latest_reasons = _check_latest_symlink(
                dst_vol,
                dst_ep,
                SyncReason.DESTINATION_LATEST_NOT_FOUND,
                SyncReason.DESTINATION_LATEST_INVALID,
                re,
            )
            reasons.extend(latest_reasons)

    return SyncStatus(
        slug=sync.slug,
        config=sync,
        source_status=src_status,
        destination_status=dst_status,
        reasons=reasons,
        destination_latest_target=dst_latest_target,
    )


def check_all_syncs(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Check volumes and syncs, caching volume checks.

    When *only_syncs* is given, only those syncs (and the
    volumes they reference) are checked.
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

    volume_statuses: dict[str, VolumeStatus] = {}
    for slug in needed_volumes:
        volume = config.volumes[slug]
        volume_statuses[slug] = check_volume(volume, re)
        if on_progress:
            on_progress(slug)

    sync_statuses: dict[str, SyncStatus] = {}
    for slug, sync in syncs.items():
        sync_statuses[slug] = check_sync(
            sync, config, volume_statuses, re, config.syncs, dry_run=dry_run
        )
        if on_progress:
            on_progress(slug)

    return volume_statuses, sync_statuses
