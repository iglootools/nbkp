"""Show orchestration: list snapshots for each sync endpoint."""

from __future__ import annotations

from ....clihelpers import Strictness
from ....config import Config
from ....config.epresolution import ResolvedEndpoints
from ....preflight import SyncStatus
from ...common import list_snapshots, read_latest_symlink
from ...errors import SnapshotOperationError
from ...models import ShowResult, SnapshotSkipReason
from .common import max_snapshots, preflight_disposition


def _disposition(
    status: SyncStatus,
    config: Config,
    strictness: Strictness,
) -> tuple[SnapshotSkipReason | None, str | None]:
    """``(skip_reason, error)``; snapshot-less syncs skip before preflight."""
    match config.destination_endpoint(status.config).snapshot_mode:
        case "none":
            return (SnapshotSkipReason.NO_SNAPSHOTS, None)
        case _:
            return preflight_disposition(status, strictness)


def _read_snapshots(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
) -> ShowResult:
    dst_ep = config.destination_endpoint(status.config)
    try:
        return ShowResult(
            sync_slug=slug,
            snapshot_mode=dst_ep.snapshot_mode,
            snapshots=tuple(list_snapshots(status.config, config, re)),
            latest=read_latest_symlink(status.config, config, resolved_endpoints=re),
            max_snapshots=max_snapshots(dst_ep),
        )
    except SnapshotOperationError as e:
        return ShowResult(
            sync_slug=slug,
            snapshot_mode=dst_ep.snapshot_mode,
            snapshots=(),
            latest=None,
            max_snapshots=max_snapshots(dst_ep),
            error=str(e),
        )


def _process_candidate(
    slug: str,
    status: SyncStatus,
    config: Config,
    re: ResolvedEndpoints,
    strictness: Strictness,
) -> ShowResult:
    """Process a single show candidate into a ShowResult."""
    match _disposition(status, config, strictness):
        case (None, None):
            return _read_snapshots(slug, status, config, re)
        case (skip_reason, error):
            dst_ep = config.destination_endpoint(status.config)
            return ShowResult(
                sync_slug=slug,
                snapshot_mode=dst_ep.snapshot_mode,
                snapshots=(),
                latest=None,
                max_snapshots=max_snapshots(dst_ep),
                skip_reason=skip_reason,
                error=error,
            )


def show_all_syncs(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[ShowResult]:
    """Show snapshot information for all eligible syncs.

    Returns a list of ShowResult for each candidate sync.
    """
    re = resolved_endpoints or {}
    return [
        _process_candidate(slug, status, config, re, strictness)
        for slug, status in sync_statuses.items()
        if not only_syncs or slug in only_syncs
    ]
