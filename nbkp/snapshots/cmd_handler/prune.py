"""Prune orchestration: candidate selection and snapshot pruning."""

from __future__ import annotations

from ...config import Config
from ...config.epresolution import ResolvedEndpoints
from ...preflight import SyncStatus
from ..models import PruneResult
from ..btrfs import prune_snapshots as btrfs_prune_snapshots
from ..common import list_snapshots
from ..hardlinks import prune_snapshots as hl_prune_snapshots


def _skip_reason(
    slug: str,
    status: SyncStatus,
    config: Config,
    only_syncs: list[str] | None = None,
) -> str | None:
    """Return skip reason, or None if prunable."""
    if only_syncs and slug not in only_syncs:
        return None  # filtered out by --sync, omit entirely
    if not status.active:
        return "inactive"
    dst_ep = config.destination_endpoint(status.config)
    match dst_ep.snapshot_mode:
        case "btrfs" | "hard-link":
            snap_cfg = (
                dst_ep.btrfs_snapshots
                if dst_ep.snapshot_mode == "btrfs"
                else dst_ep.hard_link_snapshots
            )
            return "no max-snapshots limit" if snap_cfg.max_snapshots is None else None
        case _:
            return "no snapshots configured"


def _existing_snapshot_count(
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
) -> int:
    """Count existing snapshots for a skipped sync, returning 0 on failure."""
    if (
        status.active
        and config.destination_endpoint(status.config).snapshot_mode != "none"
    ):
        try:
            return len(list_snapshots(status.config, config, re))
        except RuntimeError:
            return 0
    else:
        return 0


def _run_prune(
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
) -> list[str]:
    """Execute the appropriate prune backend, returning deleted paths."""
    dst_ep = config.destination_endpoint(status.config)
    match dst_ep.snapshot_mode:
        case "btrfs":
            assert dst_ep.btrfs_snapshots.max_snapshots is not None
            return btrfs_prune_snapshots(
                status.config,
                config,
                dst_ep.btrfs_snapshots.max_snapshots,
                dry_run=dry_run,
                resolved_endpoints=re,
            )
        case "hard-link":
            assert dst_ep.hard_link_snapshots.max_snapshots is not None
            return hl_prune_snapshots(
                status.config,
                config,
                dst_ep.hard_link_snapshots.max_snapshots,
                dry_run=dry_run,
                resolved_endpoints=re,
            )
        case _:
            return []


def _process_candidate(
    slug: str,
    status: SyncStatus,
    skip: str | None,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
) -> PruneResult:
    """Process a single prune candidate into a PruneResult."""
    if skip is not None:
        return PruneResult(
            sync_slug=slug,
            deleted=[],
            kept=_existing_snapshot_count(status, config, re),
            dry_run=dry_run,
            detail=skip,
            skipped=True,
        )
    else:
        try:
            deleted = _run_prune(status, config, re, dry_run)
            remaining = list_snapshots(status.config, config, re)
            return PruneResult(
                sync_slug=slug,
                deleted=deleted,
                kept=(len(remaining) + (len(deleted) if dry_run else 0)),
                dry_run=dry_run,
            )
        except RuntimeError as e:
            return PruneResult(
                sync_slug=slug,
                deleted=[],
                kept=0,
                dry_run=dry_run,
                detail=str(e),
            )


def prune_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[PruneResult]:
    """Prune old snapshots for all eligible syncs.

    Returns a list of PruneResult for each candidate sync.
    """
    re = resolved_endpoints or {}
    return [
        _process_candidate(slug, status, skip, config, re, dry_run)
        for slug, status in sync_statuses.items()
        if not (only_syncs and slug not in only_syncs)
        for skip in [_skip_reason(slug, status, config, only_syncs)]
    ]
