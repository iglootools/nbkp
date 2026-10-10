"""Shared candidate classification for ``snapshots show`` and ``prune``."""

from __future__ import annotations

from ....config.protocol.sync_endpoint import SyncEndpoint
from ....policy import Strictness
from ....preflight import SyncStatus
from ...models import SnapshotSkipReason


def max_snapshots(dst_ep: SyncEndpoint) -> int | None:
    """The retention limit of a sync endpoint (``None``: unlimited / none)."""
    match dst_ep.snapshot_mode:
        case "btrfs":
            return dst_ep.btrfs_snapshots.max_snapshots
        case "hard-link":
            return dst_ep.hard_link_snapshots.max_snapshots
        case _:
            return None


def _error_summary(status: SyncStatus) -> str:
    return ", ".join(e.value for e in status.errors)


def preflight_disposition(
    status: SyncStatus,
    strictness: Strictness,
) -> tuple[SnapshotSkipReason | None, str | None]:
    """Decide from preflight whether a sync is processed, skipped or failed.

    Returns ``(skip_reason, error)``; ``(None, None)`` means "process it".
    Mirrors ``run``'s strictness table: expected inactivity is a skip
    unless ``ignore-none``; infrastructure errors are a failure unless
    ``ignore-all``, which skips the sync rather than reading or deleting
    snapshots on a destination preflight could not vouch for.
    """
    match (status.active, status.is_expected_inactive(), strictness):
        case (True, _, _):
            return (None, None)
        case (False, True, Strictness.IGNORE_NONE):
            return (None, f"inactive: {_error_summary(status)}")
        case (False, True, _):
            return (SnapshotSkipReason.INACTIVE, None)
        case (False, False, Strictness.IGNORE_ALL):
            return (SnapshotSkipReason.PREFLIGHT_ERRORS, None)
        case _:
            return (None, f"preflight errors: {_error_summary(status)}")
