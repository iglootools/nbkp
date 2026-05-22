"""Preflight error strictness policy.

Lives in clihelpers (rather than preflight) so that CLI-layer code in
sibling packages such as ``disks.cli`` can read the active strictness
when picking display severity, without creating a cycle through
``preflight``.  The ``has_fatal_errors`` helper that consults
preflight statuses still lives in ``preflight.strictness``.
"""

from __future__ import annotations

import enum


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
