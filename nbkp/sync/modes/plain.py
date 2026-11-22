"""Plain sync: rsync straight into the destination, no snapshots."""

from nbkp.config import SyncConfig
from nbkp.sync.modes.common import RunContext, rsync_step
from nbkp.sync.results import SyncResult


def run_plain_sync(sync: SyncConfig, ctx: RunContext) -> SyncResult:
    match rsync_step(sync, ctx, dest_suffix=None):
        case SyncResult() as failure:
            return failure
        case proc:
            return SyncResult.succeeded(sync.slug, ctx.dry_run, proc)
