"""Prune orchestration: candidate selection and snapshot pruning."""

from __future__ import annotations

from collections.abc import Callable

from ....clihelpers import Strictness
from ....config import Config, SyncConfig
from ....config.epresolution import ResolvedEndpoints
from ....config.protocol.sync_endpoint import SyncEndpoint
from ....preflight import SyncStatus
from ...btrfs import prune_snapshots as btrfs_prune_snapshots
from ...common import list_snapshots
from ...errors import SnapshotOperationError
from ...hardlinks import prune_snapshots as hl_prune_snapshots
from ...models import PruneResult, SnapshotSkipReason
from .common import max_snapshots, preflight_disposition

type _PruneFn = Callable[..., list[str]]


def _config_skip_reason(dst_ep: SyncEndpoint) -> SnapshotSkipReason | None:
    """Skip reason that follows from the config alone, before preflight."""
    match (dst_ep.snapshot_mode, max_snapshots(dst_ep)):
        case ("none", _):
            return SnapshotSkipReason.NO_SNAPSHOTS
        case (_, None):
            return SnapshotSkipReason.NO_MAX_SNAPSHOTS
        case _:
            return None


def _prune_backend(dst_ep: SyncEndpoint) -> _PruneFn | None:
    match dst_ep.snapshot_mode:
        case "btrfs":
            return btrfs_prune_snapshots
        case "hard-link":
            return hl_prune_snapshots
        case _:
            return None


def _count_snapshots(
    sync: SyncConfig, config: Config, re: ResolvedEndpoints
) -> tuple[int, str | None]:
    """``(count, None)``, or ``(0, error)`` when the listing fails."""
    try:
        return (len(list_snapshots(sync, config, re)), None)
    except SnapshotOperationError as e:
        return (0, str(e))


def _skipped(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
    skip_reason: SnapshotSkipReason | None,
    error: str | None,
) -> PruneResult:
    """A result for a sync that is not pruned.

    For an active snapshot destination (e.g. no ``max-snapshots``), the
    existing snapshots are still counted; a failure to list them is
    reported as the result's error rather than as zero snapshots.
    """
    countable = (
        status.active
        and error is None
        and config.destination_endpoint(status.config).snapshot_mode != "none"
    )
    kept, count_error = (
        _count_snapshots(status.config, config, re) if countable else (0, None)
    )
    return PruneResult(
        sync_slug=slug,
        deleted=(),
        kept=kept,
        dry_run=dry_run,
        skip_reason=skip_reason,
        error=error or count_error,
    )


def _execute_prune(
    slug: str,
    sync: SyncConfig,
    prune_fn: _PruneFn,
    limit: int,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
) -> PruneResult:
    try:
        deleted = prune_fn(sync, config, limit, dry_run=dry_run, resolved_endpoints=re)
        remaining = list_snapshots(sync, config, re)
        return PruneResult(
            sync_slug=slug,
            deleted=tuple(deleted),
            kept=len(remaining) + (len(deleted) if dry_run else 0),
            dry_run=dry_run,
        )
    except SnapshotOperationError as e:
        return PruneResult(
            sync_slug=slug, deleted=(), kept=0, dry_run=dry_run, error=str(e)
        )


def _process_candidate(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
    strictness: Strictness,
) -> PruneResult:
    """Process a single prune candidate into a PruneResult."""
    dst_ep = config.destination_endpoint(status.config)
    config_skip = _config_skip_reason(dst_ep)
    skip_reason, error = (
        (config_skip, None)
        if config_skip is not None
        else preflight_disposition(status, strictness)
    )
    match (skip_reason, error, _prune_backend(dst_ep), max_snapshots(dst_ep)):
        case (None, None, prune_fn, int() as limit) if prune_fn is not None:
            return _execute_prune(
                slug, status.config, prune_fn, limit, config, re, dry_run
            )
        case _:
            return _skipped(slug, status, config, re, dry_run, skip_reason, error)


def prune_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[PruneResult]:
    """Prune old snapshots for all eligible syncs.

    Returns a list of PruneResult for each candidate sync.
    """
    re = resolved_endpoints or {}
    return [
        _process_candidate(slug, status, config, re, dry_run, strictness)
        for slug, status in sync_statuses.items()
        if not only_syncs or slug in only_syncs
    ]
