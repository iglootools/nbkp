"""The steps every sync mode shares: running rsync, publishing a snapshot."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from nbkp.config import Config, SyncConfig
from nbkp.fsprotocol import Snapshot
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import ssh_connection_errors
from nbkp.snapshots.common import update_latest_symlink
from nbkp.snapshots.errors import SnapshotOperationError, SnapshotPruneError
from nbkp.sync.results import SyncFailureKind, SyncResult, SyncWarning, SyncWarningKind
from nbkp.sync.rsync import ProgressMode, run_rsync


class RsyncRunner(Protocol):
    """
    Runs rsync for a sync.

    :func:`~nbkp.sync.rsync.run_rsync` in production, a fake in tests.
    """

    def __call__(
        self,
        sync: SyncConfig,
        config: Config,
        *,
        dry_run: bool,
        link_dest: str | None,
        progress: ProgressMode | None,
        on_output: Callable[[str], None] | None,
        resolved_endpoints: ResolvedEndpoints,
        dest_suffix: str | None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run rsync for *sync* and return its result; a non-zero exit is not raised.

        *on_output* receives rsync's output as it streams, when given.
        """
        ...


@dataclass(frozen=True)
class RunContext:
    """
    What a sync mode needs to run a sync.

    *rsync* and *run_command* reach the destination host; tests replace them with fakes
    instead of patching the transport.
    """

    config: Config
    dry_run: bool
    progress: ProgressMode | None
    prune: bool
    on_rsync_output: Callable[[str], None] | None
    resolved_endpoints: ResolvedEndpoints
    clock: Callable[[], datetime]
    platform: str
    rsync: RsyncRunner = run_rsync
    run_command: VolumeCommandRunner = run_on_volume


def rsync_step(
    sync: SyncConfig,
    ctx: RunContext,
    *,
    dest_suffix: str | None,
    link_dest: str | None = None,
) -> subprocess.CompletedProcess[str] | SyncResult:
    """Run rsync; a failed :class:`SyncResult` when it does not succeed."""
    try:
        proc = ctx.rsync(
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
    except ssh_connection_errors() as e:
        return SyncResult.failed(
            sync.slug,
            dry_run=ctx.dry_run,
            failure=SyncFailureKind.RSYNC,
            detail=str(e),
        )
    if proc.returncode != 0:
        return SyncResult.failed(
            sync.slug,
            dry_run=ctx.dry_run,
            failure=SyncFailureKind.RSYNC,
            detail=f"rsync exited with code {proc.returncode}",
            rsync_exit_code=proc.returncode,
            output=_rsync_output(proc),
        )
    else:
        return proc


def _rsync_output(proc: subprocess.CompletedProcess[str]) -> str:
    """
    Return rsync's stdout and stderr.

    :func:`~nbkp.sync.rsync.run_local_process` merges them into stdout (with an empty
    stderr); another runner may return them apart.
    """
    return proc.stdout + proc.stderr


def failed_after_rsync(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    ctx: RunContext,
    failure: SyncFailureKind,
    detail: str,
) -> SyncResult:
    """
    Return the failed result of a step after rsync succeeded, keeping rsync's output.

    *failure* names the step and *detail* describes how it failed.
    """
    return SyncResult.failed(
        sync.slug,
        dry_run=ctx.dry_run,
        failure=failure,
        detail=detail,
        rsync_exit_code=proc.returncode,
        output=_rsync_output(proc),
    )


def _prune(
    sync: SyncConfig,
    max_snapshots: int | None,
    prune_fn: Callable[..., list[str]],
    ctx: RunContext,
) -> tuple[tuple[str, ...] | None, tuple[SyncWarning, ...]]:
    """
    Best-effort retention after a successful snapshot.

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
            run=ctx.run_command,
        )
        return (tuple(pruned), ())
    except SnapshotOperationError as e:
        warning = SyncWarning(kind=SyncWarningKind.PRUNE, message=f"Prune failed: {e}")
        deleted = e.deleted if isinstance(e, SnapshotPruneError) else None
        return (deleted, (warning,))


def publish_snapshot(
    sync: SyncConfig,
    proc: subprocess.CompletedProcess[str],
    snapshot_path: str,
    max_snapshots: int | None,
    prune_fn: Callable[..., list[str]],
    *,
    ctx: RunContext,
) -> SyncResult:
    """
    Point ``latest`` at the new snapshot, prune, and return the sync's result.

    A failed symlink update fails the sync (``SYMLINK``); pruning, done with
    *prune_fn* down to *max_snapshots* when ``ctx.prune`` is set, only ever adds a
    warning.
    """
    try:
        update_latest_symlink(
            sync,
            ctx.config,
            Snapshot.from_path(snapshot_path),
            resolved_endpoints=ctx.resolved_endpoints,
            run=ctx.run_command,
        )
    except SnapshotOperationError as e:
        return failed_after_rsync(
            sync,
            proc,
            ctx,
            SyncFailureKind.SYMLINK,
            f"Symlink update failed: {e}",
        )
    pruned, warnings = _prune(sync, max_snapshots, prune_fn, ctx)
    return SyncResult.succeeded(
        sync.slug,
        dry_run=ctx.dry_run,
        proc=proc,
        snapshot_path=snapshot_path,
        pruned_paths=pruned,
        warnings=warnings,
    )
