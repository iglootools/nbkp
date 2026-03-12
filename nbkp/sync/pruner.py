"""Prune orchestration: candidate selection and snapshot pruning."""

from __future__ import annotations

from ..config import Config, ResolvedEndpoints
from ..preflight import SyncStatus
from .runner import PruneResult
from .snapshots.btrfs import prune_snapshots as btrfs_prune_snapshots
from .snapshots.common import list_snapshots
from .snapshots.hardlinks import prune_snapshots as hl_prune_snapshots


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

    candidates = [
        (slug, status, _skip_reason(slug, status, config, only_syncs))
        for slug, status in sync_statuses.items()
        if not (only_syncs and slug not in only_syncs)
    ]

    results: list[PruneResult] = []
    for slug, status, skip in candidates:
        match skip:
            case str():
                kept = 0
                if (
                    status.active
                    and config.destination_endpoint(status.config).snapshot_mode
                    != "none"
                ):
                    try:
                        kept = len(list_snapshots(status.config, config, re))
                    except RuntimeError:
                        pass
                results.append(
                    PruneResult(
                        sync_slug=slug,
                        deleted=[],
                        kept=kept,
                        dry_run=dry_run,
                        detail=skip,
                        skipped=True,
                    )
                )
            case None:
                dst_ep = config.destination_endpoint(status.config)
                try:
                    match dst_ep.snapshot_mode:
                        case "btrfs":
                            assert dst_ep.btrfs_snapshots.max_snapshots is not None
                            deleted = btrfs_prune_snapshots(
                                status.config,
                                config,
                                dst_ep.btrfs_snapshots.max_snapshots,
                                dry_run=dry_run,
                                resolved_endpoints=re,
                            )
                        case "hard-link":
                            assert dst_ep.hard_link_snapshots.max_snapshots is not None
                            deleted = hl_prune_snapshots(
                                status.config,
                                config,
                                dst_ep.hard_link_snapshots.max_snapshots,
                                dry_run=dry_run,
                                resolved_endpoints=re,
                            )
                        case _:
                            deleted = []
                    remaining = list_snapshots(status.config, config, re)
                    results.append(
                        PruneResult(
                            sync_slug=slug,
                            deleted=deleted,
                            kept=(len(remaining) + (len(deleted) if dry_run else 0)),
                            dry_run=dry_run,
                        )
                    )
                except RuntimeError as e:
                    results.append(
                        PruneResult(
                            sync_slug=slug,
                            deleted=[],
                            kept=0,
                            dry_run=dry_run,
                            detail=str(e),
                        )
                    )

    return results
