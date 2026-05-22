"""Preflight error strictness policy.

The :class:`Strictness` enum itself lives in :mod:`nbkp.clihelpers.strictness`
so CLI code in sibling packages can read it without creating a cycle
through ``preflight``.  This module re-exports it and owns the
preflight-status-aware :func:`has_fatal_errors` helper.
"""

from __future__ import annotations

from ..clihelpers.strictness import Strictness as Strictness
from .status import SyncStatus


def has_fatal_errors(
    sync_statuses: dict[str, SyncStatus],
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> bool:
    """Return True if any sync has errors that should abort the run.

    See :class:`Strictness` for the three modes.
    """
    match strictness:
        case Strictness.IGNORE_NONE:
            return any(not s.active for s in sync_statuses.values())
        case Strictness.IGNORE_INACTIVE:
            return any(
                not s.active and not s.is_expected_inactive()
                for s in sync_statuses.values()
            )
        case Strictness.IGNORE_ALL:
            return False
