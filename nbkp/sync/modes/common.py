"""Per-run context and the steps shared by every sync mode.

:class:`RunContext` carries the per-run parameters; the steps run rsync and
publish a completed snapshot (``latest`` update, then best-effort pruning).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ...config import Config, SyncConfig
from ...fsprotocol import Snapshot
from ...remote.endpoints import ResolvedEndpoints
from ...remote.errors import SSH_CONNECTION_ERRORS
from ...snapshots.common import update_latest_symlink
from ...snapshots.errors import SnapshotOperationError
from ..results import SyncFailureKind, SyncResult, SyncWarning, SyncWarningKind
from ..rsync import ProgressMode, run_rsync


@dataclass(frozen=True)
class RunContext:
    """Per-run parameters shared by every sync."""

    config: Config
    dry_run: bool
    progress: ProgressMode | None
    prune: bool
    on_rsync_output: Callable[[str], None] | None
    resolved_endpoints: ResolvedEndpoints
    clock: Callable[[], datetime]
    platform: str


def rsync_step(
    sync: SyncConfig,
    ctx: RunContext,
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


def failed_after_rsync(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    ctx: RunContext,
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
    ctx: RunContext,
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


def publish_snapshot(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    snapshot_path: str,
    max_snapshots: int | None,
    prune_fn: Callable[..., list[str]],
    ctx: RunContext,
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
        return failed_after_rsync(
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
