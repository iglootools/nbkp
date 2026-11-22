"""The sync modes: plain, hard-link snapshots and btrfs snapshots."""

from nbkp.preflight import SyncStatus
from nbkp.sync.modes.btrfs import run_btrfs_sync
from nbkp.sync.modes.common import RsyncRunner, RunContext
from nbkp.sync.modes.hardlink import run_hard_link_sync
from nbkp.sync.modes.plain import run_plain_sync
from nbkp.sync.results import SyncResult

__all__ = ["RsyncRunner", "RunContext", "run_single_sync"]


def run_single_sync(status: SyncStatus, ctx: RunContext) -> SyncResult:
    """
    Run one sync with the mode its destination's snapshot mode selects.

    Return its result; a failed step is reported in the result, not raised.
    """
    sync = status.config
    dst = ctx.config.destination_endpoint(sync)
    match dst.snapshot_mode:
        case "hard-link":
            return run_hard_link_sync(status, dst, ctx)
        case "btrfs":
            return run_btrfs_sync(status, dst, ctx)
        case _:
            return run_plain_sync(sync, ctx)
