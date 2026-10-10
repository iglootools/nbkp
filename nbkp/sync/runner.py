"""Sync orchestration: checks -> rsync -> snapshots."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from ..config import Config, SyncConfig, SyncEndpoint
from ..fsprotocol import SNAPSHOTS_DIR, STAGING_DIR, Snapshot
from ..ordering.graph import sort_syncs, sync_predecessors
from ..policy import Severity, Strictness, classify_severity
from ..preflight import SyncError, SyncStatus
from ..remote.endpoints import ResolvedEndpoints
from ..remote.errors import SSH_CONNECTION_ERRORS
from ..snapshots.btrfs import (
    create_snapshot,
    prune_snapshots as btrfs_prune_snapshots,
)
from ..snapshots.common import destination_volume, update_latest_symlink
from ..snapshots.errors import SnapshotOperationError
from ..snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot as hl_delete_snapshot,
    prune_snapshots as hl_prune_snapshots,
    snapshot_path_for,
)
from .rsync import ProgressMode, run_rsync

# ── Result model ─────────────────────────────────────────────


class SyncOutcome(str, Enum):
    """Outcome of a sync operation."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SyncFailureKind(str, Enum):
    """Why a sync did not succeed.  ``None`` on a successful result."""

    # FAILED: the sync ran and a step failed.
    RSYNC = "rsync"
    """rsync exited non-zero, or could not be started (SSH, spawn)."""
    MKDIR = "mkdir"
    """The hard-link snapshot directory could not be created."""
    SNAPSHOT = "snapshot"
    """The btrfs snapshot could not be taken."""
    SYMLINK = "symlink"
    """The ``latest`` symlink could not be updated."""

    # SKIPPED: preflight kept the sync from running.
    INACTIVE = "inactive"
    """Expected inactivity: a missing sentinel, an unreachable host."""
    DRY_RUN_PENDING = "dry-run-pending"
    """Dry run: the source snapshot only exists once the upstream really runs."""
    PREFLIGHT = "preflight"
    """Infrastructure errors, and presence could not be proven."""

    # CANCELLED: an upstream sync did not succeed.
    UPSTREAM_FAILED = "upstream-failed"
    """An upstream sync failed (directly or transitively)."""
    UPSTREAM_SKIPPED = "upstream-skipped"
    """An upstream sync was skipped (directly or transitively)."""


_OUTCOME_BY_FAILURE: dict[SyncFailureKind, SyncOutcome] = {
    SyncFailureKind.RSYNC: SyncOutcome.FAILED,
    SyncFailureKind.MKDIR: SyncOutcome.FAILED,
    SyncFailureKind.SNAPSHOT: SyncOutcome.FAILED,
    SyncFailureKind.SYMLINK: SyncOutcome.FAILED,
    SyncFailureKind.INACTIVE: SyncOutcome.SKIPPED,
    SyncFailureKind.DRY_RUN_PENDING: SyncOutcome.SKIPPED,
    SyncFailureKind.PREFLIGHT: SyncOutcome.SKIPPED,
    SyncFailureKind.UPSTREAM_FAILED: SyncOutcome.CANCELLED,
    SyncFailureKind.UPSTREAM_SKIPPED: SyncOutcome.CANCELLED,
}

#: Failures that originate in a step that actually ran (or in a cascade
#: from one): fatal under every strictness.
_RUNTIME_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {
        SyncFailureKind.RSYNC,
        SyncFailureKind.MKDIR,
        SyncFailureKind.SNAPSHOT,
        SyncFailureKind.SYMLINK,
        SyncFailureKind.UPSTREAM_FAILED,
    }
)

#: Non-runs rooted in expected inactivity.
_INACTIVE_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {
        SyncFailureKind.INACTIVE,
        SyncFailureKind.DRY_RUN_PENDING,
        SyncFailureKind.UPSTREAM_SKIPPED,
    }
)


class SyncWarningKind(str, Enum):
    """A best-effort step that failed without failing the sync."""

    ORPHAN_CLEANUP = "orphan-cleanup"
    PRUNE = "prune"
    SNAPSHOT_DIR_CLEANUP = "snapshot-dir-cleanup"


class SyncWarning(BaseModel):
    """A non-fatal problem encountered while running a sync."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SyncWarningKind
    message: str


class SyncResult(BaseModel):
    """Result of running a sync.

    ``failure`` says why a sync did not succeed and determines
    ``outcome``; ``detail`` is the human-readable rendering of it.
    Prefer the constructors (:meth:`succeeded`, :meth:`failed`,
    :meth:`skipped`, :meth:`cancelled`), which keep the two consistent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    outcome: SyncOutcome
    dry_run: bool
    rsync_exit_code: int = -1
    """``-1`` when rsync did not run to completion."""
    output: str = ""
    failure: SyncFailureKind | None = None
    cancelled_by: str | None = None
    """For a cancelled sync, the upstream sync whose result caused it."""
    detail: str | None = None
    snapshot_path: str | None = None
    pruned_paths: tuple[str, ...] | None = None
    warnings: tuple[SyncWarning, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.outcome is SyncOutcome.SUCCESS

    @model_validator(mode="after")
    def _check_consistency(self) -> SyncResult:
        expected = (
            SyncOutcome.SUCCESS
            if self.failure is None
            else _OUTCOME_BY_FAILURE[self.failure]
        )
        if self.outcome is not expected:
            msg = f"outcome {self.outcome.value} contradicts failure {self.failure}"
            raise ValueError(msg)
        if (self.cancelled_by is not None) != (expected is SyncOutcome.CANCELLED):
            msg = "cancelled_by is set exactly when the sync is cancelled"
            raise ValueError(msg)
        return self

    @staticmethod
    def succeeded(
        slug: str,
        dry_run: bool,
        proc: subprocess.CompletedProcess[str],
        *,
        snapshot_path: str | None = None,
        pruned_paths: tuple[str, ...] | None = None,
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.SUCCESS,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
            snapshot_path=snapshot_path,
            pruned_paths=pruned_paths,
            warnings=warnings,
        )

    @staticmethod
    def failed(
        slug: str,
        dry_run: bool,
        failure: SyncFailureKind,
        detail: str,
        *,
        rsync_exit_code: int = -1,
        output: str = "",
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.FAILED,
            dry_run=dry_run,
            rsync_exit_code=rsync_exit_code,
            output=output,
            failure=failure,
            detail=detail,
            warnings=warnings,
        )

    @staticmethod
    def skipped(
        slug: str, dry_run: bool, failure: SyncFailureKind, detail: str
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.SKIPPED,
            dry_run=dry_run,
            failure=failure,
            detail=detail,
        )

    @staticmethod
    def cancelled(
        slug: str, dry_run: bool, failure: SyncFailureKind, upstream: str
    ) -> SyncResult:
        reason = "failed" if failure is SyncFailureKind.UPSTREAM_FAILED else "skipped"
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.CANCELLED,
            dry_run=dry_run,
            failure=failure,
            cancelled_by=upstream,
            detail=f"Cancelled: upstream sync '{upstream}' {reason}",
        )

    def with_warnings(self, warnings: tuple[SyncWarning, ...]) -> SyncResult:
        """A copy with *warnings* appended."""
        return self.model_copy(update={"warnings": (*self.warnings, *warnings)})


def result_severity(
    result: SyncResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity of a sync result under *strictness*.

    Runtime failures — and cancellations caused by one — are errors under
    every strictness.  Non-runs rooted in expected inactivity follow the
    strictness policy for inactive errors, and syncs skipped for
    infrastructure errors follow the policy for infrastructure errors.
    ``ERROR`` is exactly what makes ``run`` exit non-zero.
    """
    match result.failure:
        case None:
            return Severity.OK
        case kind if kind in _RUNTIME_FAILURES:
            return Severity.ERROR
        case kind:
            return classify_severity(kind in _INACTIVE_FAILURES, strictness)


# ── Orchestration ────────────────────────────────────────────


@dataclass(frozen=True)
class _RunContext:
    """Per-run parameters shared by every sync."""

    config: Config
    dry_run: bool
    progress: ProgressMode | None
    prune: bool
    on_rsync_output: Callable[[str], None] | None
    resolved_endpoints: ResolvedEndpoints
    clock: Callable[[], datetime]
    platform: str


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
    failed = sorted(r.sync_slug for r in blocking if r.failure in _RUNTIME_FAILURES)
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
    ctx: _RunContext,
) -> SyncResult:
    cancellation = _cancellation(status.slug, upstreams, results, ctx.dry_run)
    if cancellation is not None:
        return cancellation
    elif _should_attempt(status, strictness):
        return _run_single_sync(status, ctx)
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
    ctx = _RunContext(
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


def _run_single_sync(status: SyncStatus, ctx: _RunContext) -> SyncResult:
    """Run a single sync operation."""
    sync = status.config
    dst = ctx.config.destination_endpoint(sync)
    match dst.snapshot_mode:
        case "hard-link":
            return _run_hard_link_sync(status, dst, ctx)
        case "btrfs":
            return _run_btrfs_sync(sync, dst, ctx)
        case _:
            return _run_plain_sync(sync, ctx)


# ── Steps ────────────────────────────────────────────────────


def _rsync(
    sync: SyncConfig,
    ctx: _RunContext,
    *,
    dest_suffix: str | None,
    link_dest: str | None = None,
) -> subprocess.CompletedProcess[str] | SyncResult:
    """Run rsync; a failed :class:`SyncResult` when it does not succeed."""
    try:
        proc = run_rsync(
            sync,
            ctx.config,
            dry_run=ctx.dry_run,
            link_dest=link_dest,
            progress=ctx.progress,
            on_output=ctx.on_rsync_output,
            resolved_endpoints=ctx.resolved_endpoints,
            dest_suffix=dest_suffix,
        )
    # A transport-level failure (SSH, spawn, DNS) becomes a failed SyncResult so
    # the caller reports per-sync status instead of aborting the whole run.
    # Programming errors still propagate.
    except SSH_CONNECTION_ERRORS as e:
        return SyncResult.failed(sync.slug, ctx.dry_run, SyncFailureKind.RSYNC, str(e))
    if proc.returncode != 0:
        return SyncResult.failed(
            sync.slug,
            ctx.dry_run,
            SyncFailureKind.RSYNC,
            f"rsync exited with code {proc.returncode}",
            rsync_exit_code=proc.returncode,
            output=proc.stdout + proc.stderr,
        )
    else:
        return proc


def _failed_after_rsync(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    ctx: _RunContext,
    failure: SyncFailureKind,
    detail: str,
) -> SyncResult:
    return SyncResult.failed(
        sync.slug,
        ctx.dry_run,
        failure,
        detail,
        rsync_exit_code=proc.returncode,
        output=proc.stdout,
    )


def _prune(
    sync: SyncConfig,
    max_snapshots: int | None,
    prune_fn: Callable[..., list[str]],
    ctx: _RunContext,
) -> tuple[tuple[str, ...] | None, tuple[SyncWarning, ...]]:
    """Best-effort retention after a successful snapshot.

    The new snapshot is complete and ``latest`` points to it, so a pruning
    failure only leaves extra snapshots behind: it becomes a warning, not a
    failed sync, and never aborts the remaining syncs.
    """
    if not ctx.prune or max_snapshots is None:
        return (None, ())
    try:
        pruned = prune_fn(
            sync,
            ctx.config,
            max_snapshots,
            resolved_endpoints=ctx.resolved_endpoints,
        )
        return (tuple(pruned), ())
    except SnapshotOperationError as e:
        warning = SyncWarning(kind=SyncWarningKind.PRUNE, message=f"Prune failed: {e}")
        return (None, (warning,))


def _publish_snapshot(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    snapshot_path: str,
    max_snapshots: int | None,
    prune_fn: Callable[..., list[str]],
    ctx: _RunContext,
) -> SyncResult:
    """Point ``latest`` at the completed snapshot, then prune."""
    try:
        update_latest_symlink(
            sync,
            ctx.config,
            Snapshot.from_path(snapshot_path),
            resolved_endpoints=ctx.resolved_endpoints,
        )
    except SnapshotOperationError as e:
        return _failed_after_rsync(
            sync, proc, ctx, SyncFailureKind.SYMLINK, f"Symlink update failed: {e}"
        )
    pruned, warnings = _prune(sync, max_snapshots, prune_fn, ctx)
    return SyncResult.succeeded(
        sync.slug,
        ctx.dry_run,
        proc,
        snapshot_path=snapshot_path,
        pruned_paths=pruned,
        warnings=warnings,
    )


# ── Plain ────────────────────────────────────────────────────


def _run_plain_sync(sync: SyncConfig, ctx: _RunContext) -> SyncResult:
    """Run a sync with no snapshot strategy."""
    match _rsync(sync, ctx, dest_suffix=None):
        case SyncResult() as failure:
            return failure
        case proc:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)


# ── Btrfs ────────────────────────────────────────────────────


def _run_btrfs_sync(
    sync: SyncConfig, dst: SyncEndpoint, ctx: _RunContext
) -> SyncResult:
    """Run a sync with btrfs snapshot strategy (rsync into ``staging/``)."""
    match _rsync(sync, ctx, dest_suffix=STAGING_DIR):
        case SyncResult() as failure:
            return failure
        case proc if ctx.dry_run:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)
        case proc:
            return _btrfs_post_rsync(sync, dst, proc, ctx)


def _btrfs_post_rsync(
    sync: SyncConfig,
    dst: SyncEndpoint,
    proc: subprocess.CompletedProcess[str],
    ctx: _RunContext,
) -> SyncResult:
    """Snapshot ``staging/``, update ``latest``, and prune."""
    try:
        snapshot_path = create_snapshot(
            sync,
            ctx.config,
            now=ctx.clock(),
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
        )
    except SnapshotOperationError as e:
        return _failed_after_rsync(
            sync, proc, ctx, SyncFailureKind.SNAPSHOT, f"Snapshot failed: {e}"
        )
    return _publish_snapshot(
        sync,
        proc,
        snapshot_path,
        dst.btrfs_snapshots.max_snapshots,
        btrfs_prune_snapshots,
        ctx,
    )


# ── Hard-link ────────────────────────────────────────────────


def _run_hard_link_sync(
    status: SyncStatus, dst: SyncEndpoint, ctx: _RunContext
) -> SyncResult:
    """Run a sync with hard-link snapshot strategy.

    A dry run never modifies the destination: no orphan cleanup, and
    rsync previews a transfer into a snapshot directory that is never
    created.
    """
    sync = status.config
    warnings = () if ctx.dry_run else _cleanup_orphans(sync, ctx)
    latest = status.destination_latest_snapshot
    link_dest = f"../{latest.name}" if latest else None
    match _prepare_snapshot_dir(sync, ctx):
        case SyncResult() as failure:
            return failure.with_warnings(warnings)
        case snapshot_path:
            return _rsync_into_snapshot(
                sync, dst, snapshot_path, link_dest, warnings, ctx
            )


def _cleanup_orphans(sync: SyncConfig, ctx: _RunContext) -> tuple[SyncWarning, ...]:
    """Remove snapshots left by failed syncs (best-effort: a warning)."""
    try:
        cleanup_orphaned_snapshots(
            sync, ctx.config, resolved_endpoints=ctx.resolved_endpoints
        )
        return ()
    except SnapshotOperationError as e:
        return (
            SyncWarning(
                kind=SyncWarningKind.ORPHAN_CLEANUP,
                message=f"Orphaned snapshot cleanup failed: {e}",
            ),
        )


def _prepare_snapshot_dir(sync: SyncConfig, ctx: _RunContext) -> str | SyncResult:
    """The new snapshot's path; created on disk unless dry-running."""
    now = ctx.clock()
    if ctx.dry_run:
        return snapshot_path_for(sync, ctx.config, now=now, platform=ctx.platform)
    try:
        return create_snapshot_dir(
            sync,
            ctx.config,
            now=now,
            platform=ctx.platform,
            resolved_endpoints=ctx.resolved_endpoints,
        )
    except SnapshotOperationError as e:
        return SyncResult.failed(
            sync.slug,
            ctx.dry_run,
            SyncFailureKind.MKDIR,
            f"Failed to create snapshot dir: {e}",
        )


def _rsync_into_snapshot(
    sync: SyncConfig,
    dst: SyncEndpoint,
    snapshot_path: str,
    link_dest: str | None,
    warnings: tuple[SyncWarning, ...],
    ctx: _RunContext,
) -> SyncResult:
    snapshot = Snapshot.from_path(snapshot_path)
    suffix = f"{SNAPSHOTS_DIR}/{snapshot.name}"
    match _rsync(sync, ctx, dest_suffix=suffix, link_dest=link_dest):
        case SyncResult() as failure:
            discard = _discard_snapshot_dir(snapshot_path, sync, ctx)
            return failure.with_warnings((*warnings, *discard))
        case proc if ctx.dry_run:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc, warnings=warnings)
        case proc:
            result = _publish_snapshot(
                sync,
                proc,
                snapshot_path,
                dst.hard_link_snapshots.max_snapshots,
                hl_prune_snapshots,
                ctx,
            )
            return result.with_warnings(warnings)


def _discard_snapshot_dir(
    snapshot_path: str, sync: SyncConfig, ctx: _RunContext
) -> tuple[SyncWarning, ...]:
    """Remove the new snapshot dir after a failed rsync (best-effort).

    A leftover is harmless — the next run's orphan cleanup removes it — so
    a failure is a warning.  Dry runs never created the directory.
    """
    if ctx.dry_run:
        return ()
    try:
        hl_delete_snapshot(
            snapshot_path,
            destination_volume(sync, ctx.config),
            ctx.resolved_endpoints,
        )
        return ()
    except SnapshotOperationError as e:
        return (
            SyncWarning(
                kind=SyncWarningKind.SNAPSHOT_DIR_CLEANUP,
                message=f"Failed to remove snapshot dir after rsync failure: {e}",
            ),
        )
