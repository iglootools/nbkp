from collections.abc import Callable

from nbkp.config import Config, SyncConfig
from nbkp.config.protocol.sync_endpoint import SyncEndpoint
from nbkp.policy import Strictness
from nbkp.preflight import SyncStatus
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.btrfs import prune_snapshots as btrfs_prune_snapshots
from nbkp.snapshots.common import list_snapshots
from nbkp.snapshots.disposition import preflight_disposition
from nbkp.snapshots.errors import SnapshotOperationError, SnapshotPruneError
from nbkp.snapshots.hardlinks import prune_snapshots as hl_prune_snapshots
from nbkp.snapshots.models import PruneResult, SnapshotResultError, SnapshotSkipReason

type _PruneFn = Callable[..., list[str]]


def _config_skip_reason(dst_ep: SyncEndpoint) -> SnapshotSkipReason | None:
    """Skip reason that follows from the config alone, before preflight."""
    match (dst_ep.snapshot_mode, dst_ep.max_snapshots):
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
    sync: SyncConfig,
    config: Config,
    re: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> tuple[int, SnapshotResultError | None]:
    """``(count, None)``, or ``(0, error)`` when the listing fails."""
    try:
        return (len(list_snapshots(sync, config, re, run=run)), None)
    except SnapshotOperationError as e:
        return (0, SnapshotResultError.from_operation(e))


def _skipped(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
    skip_reason: SnapshotSkipReason | None,
    error: SnapshotResultError | None,
    run: VolumeCommandRunner,
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
        _count_snapshots(status.config, config, re, run) if countable else (0, None)
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
    run: VolumeCommandRunner,
) -> PruneResult:
    try:
        deleted = prune_fn(
            sync, config, limit, dry_run=dry_run, resolved_endpoints=re, run=run
        )
        remaining = list_snapshots(sync, config, re, run=run)
        return PruneResult(
            sync_slug=slug,
            deleted=tuple(deleted),
            kept=len(remaining),
            dry_run=dry_run,
        )
    except SnapshotOperationError as e:
        # A deletion that fails partway has already removed some snapshots:
        # report them, and count what is left afresh (0 if that fails too).
        kept, _ = _count_snapshots(sync, config, re, run)
        return PruneResult(
            sync_slug=slug,
            deleted=e.deleted if isinstance(e, SnapshotPruneError) else (),
            kept=kept,
            dry_run=dry_run,
            error=SnapshotResultError.from_operation(e),
        )


def _process_candidate(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    dry_run: bool,
    strictness: Strictness,
    run: VolumeCommandRunner,
) -> PruneResult:
    dst_ep = config.destination_endpoint(status.config)
    config_skip = _config_skip_reason(dst_ep)
    skip_reason, error = (
        (config_skip, None)
        if config_skip is not None
        else preflight_disposition(status, strictness)
    )
    match (skip_reason, error, _prune_backend(dst_ep), dst_ep.max_snapshots):
        case (None, None, prune_fn, int() as limit) if prune_fn is not None:
            return _execute_prune(
                slug, status.config, prune_fn, limit, config, re, dry_run, run
            )
        case _:
            return _skipped(slug, status, config, re, dry_run, skip_reason, error, run)


def prune_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> list[PruneResult]:
    """Prune each selected sync's destination, running every listing and
    deletion through *run*."""
    re = resolved_endpoints or {}
    return [
        _process_candidate(slug, status, config, re, dry_run, strictness, run)
        for slug, status in sync_statuses.items()
        if not only_syncs or slug in only_syncs
    ]
