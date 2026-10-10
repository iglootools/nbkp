"""Per-snapshot-mode sync execution (plain, btrfs, hard-link)."""

from __future__ import annotations

from ...preflight.status.sync import SyncStatus
from ..results import SyncResult
from .btrfs import run_btrfs_sync
from .common import RunContext
from .hardlink import run_hard_link_sync
from .plain import run_plain_sync

__all__ = ["RunContext", "run_single_sync"]


def run_single_sync(status: SyncStatus, ctx: RunContext) -> SyncResult:
    """Run a single sync operation."""
    sync = status.config
    dst = ctx.config.destination_endpoint(sync)
    match dst.snapshot_mode:
        case "hard-link":
            return run_hard_link_sync(status, dst, ctx)
        case "btrfs":
            return run_btrfs_sync(sync, dst, ctx)
        case _:
            return run_plain_sync(sync, ctx)
