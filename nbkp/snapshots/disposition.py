"""Shared candidate classification for ``snapshots show`` and ``prune``,
and what their results mean for the command's outcome."""

from collections.abc import Sequence

from nbkp.policy import Strictness
from nbkp.preflight import SyncStatus, is_fatal
from nbkp.snapshots.models import (
    PruneResult,
    ShowResult,
    SnapshotErrorKind,
    SnapshotResultError,
    SnapshotSkipReason,
)


def _preflight_error(
    kind: SnapshotErrorKind, status: SyncStatus
) -> SnapshotResultError:
    return SnapshotResultError(kind=kind, preflight_errors=tuple(status.errors))


def preflight_disposition(
    status: SyncStatus,
    strictness: Strictness,
) -> tuple[SnapshotSkipReason | None, SnapshotResultError | None]:
    """Decide from preflight whether a sync is processed, skipped or failed.

    Returns ``(skip_reason, error)``; ``(None, None)`` means "process it".
    A sync is a failure when it would abort ``run`` under *strictness*, and
    skipped otherwise: under ``ignore-all`` a sync with infrastructure errors
    is skipped rather than reading or deleting snapshots on a destination
    preflight could not vouch for.
    """
    match (status.active, is_fatal(status, strictness), status.is_expected_inactive()):
        case (True, _, _):
            return (None, None)
        case (False, True, True):
            return (None, _preflight_error(SnapshotErrorKind.INACTIVE, status))
        case (False, True, False):
            return (None, _preflight_error(SnapshotErrorKind.PREFLIGHT_ERRORS, status))
        case (False, False, True):
            return (SnapshotSkipReason.INACTIVE, None)
        case (False, False, False):
            return (SnapshotSkipReason.PREFLIGHT_ERRORS, None)


def has_errors(results: Sequence[ShowResult | PruneResult]) -> bool:
    """Whether ``snapshots show`` / ``prune`` exits non-zero."""
    return any(r.error is not None for r in results)


def failed_in_preflight(
    results: Sequence[ShowResult | PruneResult],
    sync_statuses: dict[str, SyncStatus],
) -> bool:
    """Whether a sync errored because preflight found it inactive or broken.

    Those are the errors ``preflight troubleshoot`` explains; a snapshot
    operation failing on an active sync is not.
    """
    return any(
        r.error is not None and not sync_statuses[r.sync_slug].active for r in results
    )
