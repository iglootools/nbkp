"""Preflight error strictness policy."""

from __future__ import annotations

import enum

from .status import SyncStatus


class Strictness(str, enum.Enum):
    """Controls how preflight errors affect the exit code.

    - ``IGNORE_NONE``: All errors are fatal — any inactive sync
      (including missing sentinels) aborts the run.
    - ``IGNORE_INACTIVE``: Expected-inactive errors (missing sentinels,
      unreachable hosts) are silently skipped; infrastructure errors
      are still fatal.  This is the default.
    - ``IGNORE_ALL``: All preflight errors are ignored — only sync
      execution failures cause a non-zero exit.
    """

    IGNORE_NONE = "ignore-none"
    IGNORE_INACTIVE = "ignore-inactive"
    IGNORE_ALL = "ignore-all"


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
