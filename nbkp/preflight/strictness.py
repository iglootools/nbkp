"""
Apply the strictness policy to preflight sync statuses.

The :class:`~nbkp.policy.Strictness` enum is generic run policy and lives in
:mod:`nbkp.policy`; this module owns the preflight-status-aware
:func:`is_fatal` predicate and :func:`has_fatal_errors`.
"""

from nbkp.policy import Strictness
from nbkp.preflight.status.sync import SyncErrorKind, SyncStatus


def _is_disabled(status: SyncStatus) -> bool:
    """Return whether a sync is disabled: never attempted, never fatal."""
    return SyncErrorKind.DISABLED in status.errors


def is_fatal(status: SyncStatus, strictness: Strictness) -> bool:
    """
    Whether *status* should abort the run under *strictness*.

    See :class:`Strictness` for the three modes.  Disabled syncs are never
    fatal, under any mode: the runner never attempts them, so they cannot
    make a run fail (``enabled: false`` is a choice, not a problem).
    """
    match (status.active or _is_disabled(status), strictness):
        case (True, _):
            return False
        case (False, Strictness.IGNORE_NONE):
            return True
        case (False, Strictness.IGNORE_INACTIVE):
            return not status.is_expected_inactive()
        case (False, Strictness.IGNORE_ALL):
            return False


def has_fatal_errors(
    sync_statuses: dict[str, SyncStatus],
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> bool:
    """Return True if any sync has errors that should abort the run."""
    return any(is_fatal(s, strictness) for s in sync_statuses.values())
