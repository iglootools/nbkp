"""Snapshot backends: shared helpers, btrfs, and hard-link snapshots.

Re-exports all public names so that external code can import from
``nbkp.sync.snapshots`` without knowing which submodule provides
a given symbol.
"""

from __future__ import annotations

# ── conventions ──────────────────────────────────────────────
from ...fsprotocol import (
    DEVNULL_TARGET as DEVNULL_TARGET,
    LATEST_LINK as LATEST_LINK,
    SNAPSHOTS_DIR as SNAPSHOTS_DIR,
    STAGING_DIR as STAGING_DIR,
)

# ── common ───────────────────────────────────────────────────
from .common import (
    get_latest_snapshot as get_latest_snapshot,
    list_snapshots as list_snapshots,
    read_latest_symlink as read_latest_symlink,
    resolve_dest_path as resolve_dest_path,
    update_latest_symlink as update_latest_symlink,
)

# ── btrfs ────────────────────────────────────────────────────
from .btrfs import (
    create_snapshot as create_snapshot,
)

# ── hard-link ────────────────────────────────────────────────
from .hardlinks import (
    cleanup_orphaned_snapshots as cleanup_orphaned_snapshots,
    create_snapshot_dir as create_snapshot_dir,
)
