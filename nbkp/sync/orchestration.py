"""Sync orchestration: run every selected sync in dependency order.

Decides, per sync, whether to run it, skip it (inactive or broken at
preflight) or cancel it (an upstream did not succeed), and delegates the run
itself to the sync's snapshot mode (:mod:`.modes`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from nbkp.config import Config, sync_predecessors
from nbkp.ordering.graph import sort_syncs
from nbkp.policy import Strictness
from nbkp.preflight import SyncErrorKind, SyncStatus
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.sync.modes import RsyncRunner, RunContext, run_single_sync
from nbkp.sync.results import (
    DISABLED_FAILURES,
    RUNTIME_FAILURES,
    SyncFailureKind,
    SyncResult,
)
from nbkp.sync.rsync import ProgressMode, run_rsync


def _presence_proven(status: SyncStatus) -> bool:
    """Whether preflight observed every sentinel of an enabled sync.

    Endpoint diagnostics only exist once the volume is reachable and its
    ``.nbkp-vol`` sentinel present, so both endpoint sentinels being
    observed proves all four.  A dry-run-pending source has no snapshot to
    read yet, so it is never attempted.
    """
    src = status.source_endpoint_status.diagnostics
    dst = status.destination_endpoint_status.diagnostics
    return (
        status.config.enabled
        and src is not None
        and src.sentinel_exists
        and dst is not None
        and dst.sentinel_exists
        and SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING not in status.errors
    )


def _should_attempt(status: SyncStatus, strictness: Strictness) -> bool:
    """Active syncs run; ``ignore-all`` also attempts broken ones.

    ``ignore-all`` attempts a sync with preflight errors only when its
    presence is proven: skipping the sentinel guarantee could write a
    backup onto an unmounted mountpoint or the wrong drive.
    """
    return status.active or (
        strictness is Strictness.IGNORE_ALL and _presence_proven(status)
    )


def _skip_kind(status: SyncStatus) -> SyncFailureKind:
    if SyncErrorKind.DISABLED in status.errors:
        return SyncFailureKind.DISABLED
    elif SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING in status.errors:
        return SyncFailureKind.DRY_RUN_PENDING
    elif status.is_expected_inactive():
        return SyncFailureKind.INACTIVE
    else:
        return SyncFailureKind.PREFLIGHT


def _blocks_downstream(result: SyncResult) -> bool:
    """Dry-run-pending skips do not cascade: a real run would succeed."""
    return not result.success and result.failure is not SyncFailureKind.DRY_RUN_PENDING


def _cancellation(
    slug: str,
    upstreams: set[str],
    results: dict[str, SyncResult],
    dry_run: bool,
) -> SyncResult | None:
    """A cancelled result when an upstream did not succeed, else ``None``.

    A failed upstream takes precedence over a skipped one, and a skipped one
    over a disabled one, so the cancellation is as severe as its most severe
    root cause: fatal whenever one is a runtime failure, never fatal when
    every one is a disabled sync.
    """
    blocking = [
        results[u] for u in upstreams if u in results and _blocks_downstream(results[u])
    ]
    failed = sorted(r.sync_slug for r in blocking if r.failure in RUNTIME_FAILURES)
    skipped = sorted(
        r.sync_slug for r in blocking if r.failure not in DISABLED_FAILURES
    )
    disabled = sorted(r.sync_slug for r in blocking)
    match (failed, skipped, disabled):
        case ([first, *_], _, _):
            return SyncResult.cancelled(
                slug, dry_run, SyncFailureKind.UPSTREAM_FAILED, first
            )
        case ([], [first, *_], _):
            return SyncResult.cancelled(
                slug, dry_run, SyncFailureKind.UPSTREAM_SKIPPED, first
            )
        case ([], [], [first, *_]):
            return SyncResult.cancelled(
                slug, dry_run, SyncFailureKind.UPSTREAM_DISABLED, first
            )
        case _:
            return None


def _execute_or_skip(
    status: SyncStatus,
    upstreams: set[str],
    results: dict[str, SyncResult],
    strictness: Strictness,
    ctx: RunContext,
) -> SyncResult:
    cancellation = _cancellation(status.slug, upstreams, results, ctx.dry_run)
    if cancellation is not None:
        return cancellation
    elif _should_attempt(status, strictness):
        return run_single_sync(status, ctx)
    else:
        # With cascade errors, status.errors is self-describing: sync-level
        # errors plus pointers to the inactive lower layers.
        return SyncResult.skipped(
            status.slug, ctx.dry_run, _skip_kind(status), tuple(status.errors)
        )


@dataclass(frozen=True)
class RunOptions:
    """How a run executes its syncs, chosen once at the entry point.

    *clock* names snapshots (read once per snapshot-enabled sync) and
    *platform* (``sys.platform``) decides the macOS-safe snapshot name form.
    *only_syncs* selects syncs (all when ``None``).  *rsync* and
    *run_command* run the transfer and the commands on the volume hosts;
    tests replace them with fakes instead of patching the transport.
    """

    clock: Callable[[], datetime]
    platform: str
    strictness: Strictness = Strictness.IGNORE_INACTIVE
    dry_run: bool = False
    only_syncs: list[str] | None = None
    progress: ProgressMode | None = None
    prune: bool = True
    resolved_endpoints: ResolvedEndpoints | None = None
    rsync: RsyncRunner = run_rsync
    run_command: VolumeCommandRunner = run_on_volume


def run_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    options: RunOptions,
    *,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
) -> list[SyncResult]:
    """Run all (or selected) syncs in dependency order.

    Expects pre-computed sync statuses from ``check_all_syncs``.
    """
    only_syncs = options.only_syncs
    selected = {
        s: st for s, st in sync_statuses.items() if not only_syncs or s in only_syncs
    }
    selected_syncs = {s: config.syncs[s] for s in selected}
    predecessors = sync_predecessors(selected_syncs)
    ctx = RunContext(
        config=config,
        dry_run=options.dry_run,
        progress=options.progress,
        prune=options.prune,
        on_rsync_output=on_rsync_output,
        resolved_endpoints=options.resolved_endpoints or {},
        clock=options.clock,
        platform=options.platform,
        rsync=options.rsync,
        run_command=options.run_command,
    )

    # An explicit loop rather than a comprehension: each sync's
    # cancellation depends on the results of the syncs before it in
    # topological order, and the callbacks must fire in that order.
    results: dict[str, SyncResult] = {}
    for slug in sort_syncs(selected_syncs):
        if on_sync_start:
            on_sync_start(slug)
        result = _execute_or_skip(
            selected[slug],
            predecessors.get(slug, set()),
            results,
            options.strictness,
            ctx,
        )
        results[slug] = result
        if on_sync_end:
            on_sync_end(slug, result)
    return list(results.values())
