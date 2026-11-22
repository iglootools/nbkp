"""
Snapshot backends (btrfs, hard-link), their shared helpers, and show/prune.

Re-exports all public names so that external code can import from
``nbkp.snapshots`` without knowing which submodule provides
a given symbol.
"""

from nbkp.fsprotocol import (
    DEVNULL_TARGET as DEVNULL_TARGET,
    LATEST_LINK as LATEST_LINK,
    SNAPSHOTS_DIR as SNAPSHOTS_DIR,
    STAGING_DIR as STAGING_DIR,
)
from nbkp.snapshots.btrfs import create_snapshot as create_snapshot
from nbkp.snapshots.common import (
    list_snapshots as list_snapshots,
    read_latest_symlink as read_latest_symlink,
    resolve_dest_path as resolve_dest_path,
    update_latest_symlink as update_latest_symlink,
)
from nbkp.snapshots.errors import (
    SnapshotOp as SnapshotOp,
    SnapshotOperationError as SnapshotOperationError,
)
from nbkp.snapshots.hardlinks import (
    cleanup_orphaned_snapshots as cleanup_orphaned_snapshots,
    create_snapshot_dir as create_snapshot_dir,
)
from nbkp.snapshots.prune import prune_all_syncs as prune_all_syncs
from nbkp.snapshots.show import show_all_syncs as show_all_syncs
