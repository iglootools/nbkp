"""Show orchestration: list snapshots for each sync endpoint."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ...config import Config
from ...config.epresolution import ResolvedEndpoints
from ...config.protocol.sync_endpoint import SyncEndpoint
from ...fsprotocol import Snapshot
from ...preflight import SyncStatus
from ..common import list_snapshots, read_latest_symlink


class ShowResult(BaseModel):
    """Result of showing snapshots for a sync."""

    sync_slug: str
    snapshot_mode: str
    snapshots: list[Snapshot]
    latest: Snapshot | None
    max_snapshots: int | None
    detail: Optional[str] = None
    skipped: bool = False


def _max_snapshots(dst_ep: SyncEndpoint) -> int | None:
    """Extract the retention limit from a sync endpoint."""
    match dst_ep.snapshot_mode:
        case "btrfs":
            return dst_ep.btrfs_snapshots.max_snapshots
        case "hard-link":
            return dst_ep.hard_link_snapshots.max_snapshots
        case _:
            return None


def _skip_reason(
    slug: str,
    status: SyncStatus,
    config: Config,
    only_syncs: list[str] | None = None,
) -> str | None:
    """Return skip reason, or None if showable."""
    if only_syncs and slug not in only_syncs:
        return None  # filtered out by --sync, omit entirely
    if not status.active:
        return "inactive"
    dst_ep = config.destination_endpoint(status.config)
    match dst_ep.snapshot_mode:
        case "btrfs" | "hard-link":
            return None
        case _:
            return "no snapshots configured"


def _process_candidate(
    slug: str,
    status: SyncStatus,
    skip: str | None,
    config: Config,
    re: ResolvedEndpoints,
) -> ShowResult:
    """Process a single show candidate into a ShowResult."""
    dst_ep = config.destination_endpoint(status.config)
    if skip is not None:
        return ShowResult(
            sync_slug=slug,
            snapshot_mode=dst_ep.snapshot_mode,
            snapshots=[],
            latest=None,
            max_snapshots=_max_snapshots(dst_ep),
            detail=skip,
            skipped=True,
        )
    else:
        try:
            snapshots = list_snapshots(status.config, config, re)
            latest = read_latest_symlink(status.config, config, resolved_endpoints=re)
            return ShowResult(
                sync_slug=slug,
                snapshot_mode=dst_ep.snapshot_mode,
                snapshots=snapshots,
                latest=latest,
                max_snapshots=_max_snapshots(dst_ep),
            )
        except RuntimeError as e:
            return ShowResult(
                sync_slug=slug,
                snapshot_mode=dst_ep.snapshot_mode,
                snapshots=[],
                latest=None,
                max_snapshots=_max_snapshots(dst_ep),
                detail=str(e),
            )


def show_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> list[ShowResult]:
    """Show snapshot information for all eligible syncs.

    Returns a list of ShowResult for each candidate sync.
    """
    re = resolved_endpoints or {}
    return [
        _process_candidate(slug, status, skip, config, re)
        for slug, status in sync_statuses.items()
        if not (only_syncs and slug not in only_syncs)
        for skip in [_skip_reason(slug, status, config, only_syncs)]
    ]
