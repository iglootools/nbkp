"""Sync orchestration: checks -> rsync -> snapshots."""

from __future__ import annotations

import shutil
import subprocess
from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, model_validator

from .snapshots.btrfs import (
    create_snapshot,
    prune_snapshots as btrfs_prune_snapshots,
)
from .snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    prune_snapshots as hl_prune_snapshots,
)
from .snapshots.common import update_latest_symlink
from ..config import Config, ResolvedEndpoints
from ..conventions import SNAPSHOTS_DIR, STAGING_DIR, Snapshot
from ..preflight import SyncError, SyncStatus
from .rsync import ProgressMode, run_rsync


class SyncOutcome(str, Enum):
    """Outcome of a sync operation."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SyncResult(BaseModel):
    """Result of running a sync."""

    sync_slug: str
    success: bool
    dry_run: bool
    rsync_exit_code: int
    output: str
    outcome: SyncOutcome = SyncOutcome.SUCCESS
    snapshot_path: Optional[str] = None
    pruned_paths: Optional[list[str]] = None
    detail: Optional[str] = None

    @model_validator(mode="after")
    def _derive_outcome(self) -> SyncResult:
        """Default outcome from success when not explicitly set."""
        if not self.success and self.outcome == SyncOutcome.SUCCESS:
            self.outcome = SyncOutcome.FAILED
        return self


class PruneResult(BaseModel):
    """Result of pruning snapshots for a sync."""

    sync_slug: str
    deleted: list[str]
    kept: int
    dry_run: bool
    detail: Optional[str] = None
    skipped: bool = False


def run_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[SyncResult]:
    """Run all (or selected) syncs.

    Expects pre-computed sync statuses from ``check_all_syncs``.
    """

    results: list[SyncResult] = []

    from ..ordering.graph import sort_syncs, sync_predecessors

    selected = (
        {s: st for s, st in sync_statuses.items() if s in only_syncs}
        if only_syncs
        else sync_statuses
    )

    selected_syncs = {s: config.syncs[s] for s in selected}
    ordered_slugs = sort_syncs(selected_syncs)
    predecessors = sync_predecessors(selected_syncs)
    failed: set[str] = set()

    for slug in ordered_slugs:
        status = selected[slug]
        if on_sync_start:
            on_sync_start(slug)

        # Check if any upstream sync failed
        failed_deps = predecessors.get(slug, set()) & failed
        if failed_deps:
            dep = sorted(failed_deps)[0]
            result = SyncResult(
                sync_slug=slug,
                success=False,
                dry_run=dry_run,
                rsync_exit_code=-1,
                output="",
                outcome=SyncOutcome.CANCELLED,
                detail=f"Cancelled: upstream sync '{dep}' failed",
            )
        elif not status.active:
            result = SyncResult(
                sync_slug=slug,
                success=False,
                dry_run=dry_run,
                rsync_exit_code=-1,
                output="",
                outcome=SyncOutcome.SKIPPED,
                detail=(
                    "Sync not active: " + ", ".join(r.value for r in status.errors)
                ),
            )
        else:
            result = _run_single_sync(
                slug,
                status,
                config,
                dry_run,
                progress,
                prune,
                on_rsync_output,
                resolved_endpoints,
            )

        # Dry-run-pending skips (skipped syncs because of DRY_RUN_SOURCE_SNAPSHOT_PENDING) should not cascade to downstream syncs:
        # the chain would succeed in a real run.
        is_dry_run_pending = (
            result.outcome == SyncOutcome.SKIPPED
            and status.errors == [SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING]
        )
        if not result.success and not is_dry_run_pending:
            failed.add(slug)

        results.append(result)
        if on_sync_end:
            on_sync_end(slug, result)

    return results


def _run_single_sync(
    slug: str,
    status: SyncStatus,
    config: Config,
    dry_run: bool,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_rsync_output: Callable[[str], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> SyncResult:
    """Run a single sync operation."""
    sync = status.config

    dst = config.destination_endpoint(sync)
    match dst.snapshot_mode:
        case "hard-link":
            return _run_hard_link_sync(
                slug,
                sync,
                status,
                config,
                dry_run,
                progress,
                prune,
                on_rsync_output,
                resolved_endpoints,
            )
        case "btrfs":
            return _run_btrfs_sync(
                slug,
                sync,
                config,
                dry_run,
                progress,
                prune,
                on_rsync_output,
                resolved_endpoints,
            )
        case _:
            return _run_plain_sync(
                slug,
                sync,
                config,
                dry_run,
                progress,
                on_rsync_output,
                resolved_endpoints,
            )


def _run_plain_sync(
    slug: str,
    sync: object,
    config: Config,
    dry_run: bool,
    progress: ProgressMode | None,
    on_rsync_output: Callable[[str], None] | None,
    resolved_endpoints: ResolvedEndpoints | None,
) -> SyncResult:
    """Run a sync with no snapshot strategy."""
    from ..config import SyncConfig

    assert isinstance(sync, SyncConfig)
    try:
        proc = run_rsync(
            sync,
            config,
            dry_run=dry_run,
            progress=progress,
            on_output=on_rsync_output,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=None,
        )
    except Exception as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=-1,
            output="",
            detail=str(e),
        )

    if proc.returncode != 0:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout + proc.stderr,
            detail=f"rsync exited with code {proc.returncode}",
        )
    else:
        return SyncResult(
            sync_slug=slug,
            success=True,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
        )


def _run_btrfs_sync(
    slug: str,
    sync: object,
    config: Config,
    dry_run: bool,
    progress: ProgressMode | None,
    prune: bool,
    on_rsync_output: Callable[[str], None] | None,
    resolved_endpoints: ResolvedEndpoints | None,
) -> SyncResult:
    """Run a sync with btrfs snapshot strategy."""
    from ..config import SyncConfig

    assert isinstance(sync, SyncConfig)
    try:
        proc = run_rsync(
            sync,
            config,
            dry_run=dry_run,
            progress=progress,
            on_output=on_rsync_output,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=STAGING_DIR,
        )
    except Exception as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=-1,
            output="",
            detail=str(e),
        )

    if proc.returncode != 0:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout + proc.stderr,
            detail=f"rsync exited with code {proc.returncode}",
        )
    else:
        return _btrfs_post_rsync(
            slug, sync, config, proc, dry_run, prune, resolved_endpoints
        )


def _btrfs_post_rsync(
    slug: str,
    sync: object,
    config: Config,
    proc: subprocess.CompletedProcess[str],
    dry_run: bool,
    prune: bool,
    resolved_endpoints: ResolvedEndpoints | None,
) -> SyncResult:
    """Create btrfs snapshot, update symlink, and optionally prune after successful rsync."""
    from ..config import SyncConfig

    assert isinstance(sync, SyncConfig)

    if dry_run:
        return SyncResult(
            sync_slug=slug,
            success=True,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
        )

    try:
        snapshot_path = create_snapshot(
            sync,
            config,
            resolved_endpoints=resolved_endpoints,
        )
    except RuntimeError as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
            detail=f"Snapshot failed: {e}",
        )

    snapshot = Snapshot.from_path(snapshot_path)
    try:
        update_latest_symlink(
            sync,
            config,
            snapshot,
            resolved_endpoints=resolved_endpoints,
        )
    except RuntimeError as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
            detail=f"Symlink update failed: {e}",
        )

    dst = config.destination_endpoint(sync)
    btrfs_cfg = dst.btrfs_snapshots
    pruned_paths = (
        btrfs_prune_snapshots(
            sync,
            config,
            btrfs_cfg.max_snapshots,
            resolved_endpoints=resolved_endpoints,
        )
        if prune and btrfs_cfg.max_snapshots is not None
        else None
    )

    return SyncResult(
        sync_slug=slug,
        success=True,
        dry_run=dry_run,
        rsync_exit_code=proc.returncode,
        output=proc.stdout,
        snapshot_path=snapshot_path,
        pruned_paths=pruned_paths,
    )


def _run_hard_link_sync(
    slug: str,
    sync: object,
    status: SyncStatus,
    config: Config,
    dry_run: bool,
    progress: ProgressMode | None,
    prune: bool,
    on_rsync_output: Callable[[str], None] | None,
    resolved_endpoints: ResolvedEndpoints | None,
) -> SyncResult:
    """Run a sync with hard-link snapshot strategy."""
    from ..config import SyncConfig

    assert isinstance(sync, SyncConfig)
    dst = config.destination_endpoint(sync)
    hl_cfg = dst.hard_link_snapshots

    # 1. Clean up orphaned snapshots from failed syncs
    try:
        cleanup_orphaned_snapshots(sync, config, resolved_endpoints=resolved_endpoints)
    except Exception:
        pass  # Best-effort cleanup

    # 2. Determine link-dest from latest complete snapshot
    link_dest = (
        f"../{status.destination_latest_snapshot.name}"
        if status.destination_latest_snapshot
        else None
    )

    # 3. Create new snapshot directory
    try:
        snapshot_path = create_snapshot_dir(
            sync, config, resolved_endpoints=resolved_endpoints
        )
    except RuntimeError as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=-1,
            output="",
            detail=f"Failed to create snapshot dir: {e}",
        )
    snapshot = Snapshot.from_path(snapshot_path)

    # 4. Run rsync into the snapshot directory
    try:
        proc = run_rsync(
            sync,
            config,
            dry_run=dry_run,
            link_dest=link_dest,
            progress=progress,
            on_output=on_rsync_output,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=f"{SNAPSHOTS_DIR}/{snapshot.name}",
        )
    except Exception as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=-1,
            output="",
            detail=str(e),
        )

    if proc.returncode != 0:
        # Clean up the empty snapshot dir on failure
        _cleanup_snapshot_dir(snapshot_path, sync, config, resolved_endpoints)
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout + proc.stderr,
            detail=f"rsync exited with code {proc.returncode}",
        )
    else:
        return _hl_post_rsync(
            slug,
            sync,
            config,
            proc,
            snapshot_path,
            snapshot,
            dry_run,
            prune,
            hl_cfg,
            resolved_endpoints,
        )


def _hl_post_rsync(
    slug: str,
    sync: object,
    config: Config,
    proc: subprocess.CompletedProcess[str],
    snapshot_path: str,
    snapshot: Snapshot,
    dry_run: bool,
    prune: bool,
    hl_cfg: object,
    resolved_endpoints: ResolvedEndpoints | None,
) -> SyncResult:
    """Update symlink, prune, or clean up after successful hard-link rsync."""
    from ..config import SyncConfig
    from ..config.protocol.sync_endpoint import HardLinkSnapshotConfig

    assert isinstance(sync, SyncConfig)
    assert isinstance(hl_cfg, HardLinkSnapshotConfig)

    if dry_run:
        _cleanup_snapshot_dir(snapshot_path, sync, config, resolved_endpoints)
        return SyncResult(
            sync_slug=slug,
            success=True,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
        )

    try:
        update_latest_symlink(
            sync,
            config,
            snapshot,
            resolved_endpoints=resolved_endpoints,
        )
    except RuntimeError as e:
        return SyncResult(
            sync_slug=slug,
            success=False,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
            detail=f"Symlink update failed: {e}",
        )

    pruned_paths = (
        hl_prune_snapshots(
            sync,
            config,
            hl_cfg.max_snapshots,
            resolved_endpoints=resolved_endpoints,
        )
        if prune and hl_cfg.max_snapshots is not None
        else None
    )

    return SyncResult(
        sync_slug=slug,
        success=True,
        dry_run=dry_run,
        rsync_exit_code=proc.returncode,
        output=proc.stdout,
        snapshot_path=snapshot_path,
        pruned_paths=pruned_paths,
    )


def _cleanup_snapshot_dir(
    snapshot_path: str,
    sync: object,
    config: Config,
    resolved_endpoints: ResolvedEndpoints | None,
) -> None:
    """Remove a snapshot directory (best-effort cleanup)."""
    from ..config import LocalVolume, RemoteVolume, SyncConfig

    assert isinstance(sync, SyncConfig)
    dst = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst.volume]
    try:
        match dst_vol:
            case LocalVolume():
                shutil.rmtree(snapshot_path, ignore_errors=True)
            case RemoteVolume():
                from ..remote import run_remote_command

                re = resolved_endpoints or {}
                ep = re[dst_vol.slug]
                run_remote_command(
                    ep.server,
                    ["rm", "-rf", snapshot_path],
                    ep.proxy_chain,
                )
    except Exception:
        pass  # Best-effort cleanup
