"""Apply the strictness policy to preflight sync statuses.

The :class:`~nbkp.policy.Strictness` enum is generic run policy and lives in
:mod:`nbkp.policy`; this module owns the preflight-status-aware
:func:`has_fatal_errors` helper.
"""

from __future__ import annotations

from ..policy import Strictness
from .status import SyncError, SyncStatus


def _is_disabled(status: SyncStatus) -> bool:
    """A sync switched off in the config: never attempted, never fatal."""
    return SyncError.DISABLED in status.errors


def has_fatal_errors(
    sync_statuses: dict[str, SyncStatus],
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> bool:
    """Return True if any sync has errors that should abort the run.

    See :class:`Strictness` for the three modes.  Disabled syncs are
    excluded under every mode: the runner never attempts them, so they
    cannot make a run fail (``enabled: false`` is a choice, not a problem).
    """
    considered = [s for s in sync_statuses.values() if not _is_disabled(s)]
    match strictness:
        case Strictness.IGNORE_NONE:
            return any(not s.active for s in considered)
        case Strictness.IGNORE_INACTIVE:
            return any(
                not s.active and not s.is_expected_inactive() for s in considered
            )
        case Strictness.IGNORE_ALL:
            return False
