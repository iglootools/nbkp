"""Delete the snapshots beyond each sync's ``max-snapshots`` limit."""

from nbkp.config import Config, SyncConfig
from nbkp.config.protocol.sync_endpoint import SyncEndpoint
from nbkp.policy import Strictness
from nbkp.preflight import SyncStatus
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.snapshots.btrfs import prune_snapshots as btrfs_prune_snapshots
from nbkp.snapshots.common import PruneSnapshotsFn, list_snapshots
from nbkp.snapshots.disposition import preflight_disposition
from nbkp.snapshots.errors import SnapshotOperationError, SnapshotPruneError
from nbkp.snapshots.hardlinks import prune_snapshots as hl_prune_snapshots
from nbkp.snapshots.models import PruneResult, SnapshotResultError, SnapshotSkipReason


def _config_skip_reason(dst_ep: SyncEndpoint) -> SnapshotSkipReason | None:
    """Skip reason that follows from the config alone, before preflight."""
    match (dst_ep.snapshot_mode, dst_ep.max_snapshots):
        case ("none", _):
            return SnapshotSkipReason.NO_SNAPSHOTS
        case (_, None):
            return SnapshotSkipReason.NO_MAX_SNAPSHOTS
        case _:
            return None


def _prune_backend(dst_ep: SyncEndpoint) -> PruneSnapshotsFn | None:
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
    resolved: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> tuple[int | None, SnapshotResultError | None]:
    """``(count, None)``, or ``(None, error)`` when the listing fails."""
    try:
        return (len(list_snapshots(sync, config, resolved, run=run)), None)
    except SnapshotOperationError as e:
        return (None, SnapshotResultError.from_operation(e))


def _skipped(
    slug: str,
    status: SyncStatus,
    config: Config,
    resolved: ResolvedEndpoints,
    *,
    dry_run: bool,
    skip_reason: SnapshotSkipReason | None,
    error: SnapshotResultError | None,
    run: VolumeCommandRunner,
) -> PruneResult:
    """
    Return a result for a sync that is not pruned.

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
        _count_snapshots(status.config, config, resolved, run)
        if countable
        else (0, None)
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
    prune_fn: PruneSnapshotsFn,
    limit: int,
    config: Config,
    *,
    resolved: ResolvedEndpoints,
    dry_run: bool,
    run: VolumeCommandRunner,
) -> PruneResult:
    """
    Prune *sync*, then count the snapshots left.

    The count runs apart from the deletion, so a count that fails never hides the
    snapshots already deleted: it becomes the result's error, with ``kept`` unknown.
    A deletion that fails partway reports the snapshots it removed, and its own
    error, which outranks a failed count.
    """
    try:
        deleted = tuple(
            prune_fn(
                sync,
                config,
                limit,
                dry_run=dry_run,
                resolved_endpoints=resolved,
                run=run,
            ),
        )
    except SnapshotOperationError as e:
        kept, _ = _count_snapshots(sync, config, resolved, run)
        return PruneResult(
            sync_slug=slug,
            deleted=e.deleted if isinstance(e, SnapshotPruneError) else (),
            kept=kept,
            dry_run=dry_run,
            error=SnapshotResultError.from_operation(e),
        )
    else:
        kept, count_error = _count_snapshots(sync, config, resolved, run)
        return PruneResult(
            sync_slug=slug,
            deleted=deleted,
            kept=kept,
            dry_run=dry_run,
            error=count_error,
        )


def _process_candidate(
    slug: str,
    status: SyncStatus,
    config: Config,
    resolved: ResolvedEndpoints,
    *,
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
                slug,
                status.config,
                prune_fn,
                limit,
                config,
                resolved=resolved,
                dry_run=dry_run,
                run=run,
            )
        case _:
            return _skipped(
                slug,
                status,
                config,
                resolved,
                dry_run=dry_run,
                skip_reason=skip_reason,
                error=error,
                run=run,
            )


def prune_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    *,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    run: VolumeCommandRunner = run_on_volume,
) -> list[PruneResult]:
    """
    Prune each selected sync's destination.

    Every listing and deletion runs through *run*.
    """
    resolved = resolved_endpoints
    return [
        _process_candidate(
            slug,
            status,
            config,
            resolved,
            dry_run=dry_run,
            strictness=strictness,
            run=run,
        )
        for slug, status in sync_statuses.items()
        if not only_syncs or slug in only_syncs
    ]
