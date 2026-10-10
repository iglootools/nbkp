"""Sync orchestration: run every selected sync in dependency order.

Decides, per sync, whether to run it, skip it (inactive or broken at
preflight) or cancel it (an upstream did not succeed), and delegates the run
itself to the sync's snapshot mode (:mod:`.modes`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..config import Config
from ..ordering.graph import sort_syncs, sync_predecessors
from ..policy import Strictness
from ..preflight.status.sync import SyncError, SyncStatus
from ..remote.endpoints import ResolvedEndpoints
from .modes import RunContext, run_single_sync
from .results import RUNTIME_FAILURES, SyncFailureKind, SyncResult
from .rsync import ProgressMode


def _collect_all_errors(status: SyncStatus) -> str:
    """Collect error messages from sync-level errors into a single string.

    With cascade errors, ``status.errors`` is self-describing — it
    contains sync-level errors plus cascade pointers to inactive lower
    layers.  No need to walk the full 4-layer chain.
    """
    return ", ".join(e.value for e in status.errors) or "unknown"


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
        and SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING not in status.errors
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
    if SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING in status.errors:
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

    A failed upstream takes precedence over a skipped one, so the
    cancellation is fatal whenever any root cause is a runtime failure.
    """
    blocking = [
        results[u] for u in upstreams if u in results and _blocks_downstream(results[u])
    ]
    failed = sorted(r.sync_slug for r in blocking if r.failure in RUNTIME_FAILURES)
    skipped = sorted(r.sync_slug for r in blocking)
    match (failed, skipped):
        case ([first, *_], _):
            return SyncResult.cancelled(
                slug, dry_run, SyncFailureKind.UPSTREAM_FAILED, first
            )
        case ([], [first, *_]):
            return SyncResult.cancelled(
                slug, dry_run, SyncFailureKind.UPSTREAM_SKIPPED, first
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
        return SyncResult.skipped(
            status.slug,
            ctx.dry_run,
            _skip_kind(status),
            "Sync not active: " + _collect_all_errors(status),
        )


def run_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    *,
    clock: Callable[[], datetime],
    platform: str,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[SyncResult]:
    """Run all (or selected) syncs in dependency order.

    Expects pre-computed sync statuses from ``check_all_syncs``.  *clock*
    names snapshots (read once per snapshot-enabled sync) and *platform*
    (``sys.platform``) decides the macOS-safe snapshot name form; both are
    supplied by the entry point.
    """
    selected = {
        s: st for s, st in sync_statuses.items() if not only_syncs or s in only_syncs
    }
    selected_syncs = {s: config.syncs[s] for s in selected}
    predecessors = sync_predecessors(selected_syncs)
    ctx = RunContext(
        config=config,
        dry_run=dry_run,
        progress=progress,
        prune=prune,
        on_rsync_output=on_rsync_output,
        resolved_endpoints=resolved_endpoints or {},
        clock=clock,
        platform=platform,
    )

    # An explicit loop rather than a comprehension: each sync's
    # cancellation depends on the results of the syncs before it in
    # topological order, and the callbacks must fire in that order.
    results: dict[str, SyncResult] = {}
    for slug in sort_syncs(selected_syncs):
        if on_sync_start:
            on_sync_start(slug)
        result = _execute_or_skip(
            selected[slug], predecessors.get(slug, set()), results, strictness, ctx
        )
        results[slug] = result
        if on_sync_end:
            on_sync_end(slug, result)
    return list(results.values())
