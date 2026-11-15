"""Shared candidate classification for ``snapshots show`` and ``prune``."""

from ....policy import Strictness
from ....preflight import SyncStatus, is_fatal
from ...models import SnapshotErrorKind, SnapshotResultError, SnapshotSkipReason


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
