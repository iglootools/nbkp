"""Plain sync: rsync straight into the destination, no snapshots."""

from nbkp.config import SyncConfig
from nbkp.sync.modes.common import RunContext, rsync_step, rsync_succeeded
from nbkp.sync.results import SyncResult


def run_plain_sync(sync: SyncConfig, ctx: RunContext) -> SyncResult:
    """Rsync *sync* straight into its destination and return the result."""
    match rsync_step(sync, ctx, dest_suffix=None):
        case SyncResult() as failure:
            return failure
        case proc:
            return rsync_succeeded(sync, proc, ctx)
