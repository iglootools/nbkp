"""Plain sync: rsync straight into the destination, no snapshots."""

from __future__ import annotations

from ...config import SyncConfig
from ..results import SyncResult
from .common import RunContext, rsync_step


def run_plain_sync(sync: SyncConfig, ctx: RunContext) -> SyncResult:
    """Run a sync with no snapshot strategy."""
    match rsync_step(sync, ctx, dest_suffix=None):
        case SyncResult() as failure:
            return failure
        case proc:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)
