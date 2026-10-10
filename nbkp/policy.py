"""Run policy: how strict to be about findings, and how bad each one is.

Decision logic shared by preflight, the mount lifecycle and the sync runner.
Each domain owns the predicate "is this finding an expected, inactive state?"
(preflight uses its ``INACTIVE_*_ERRORS`` frozensets, the mount lifecycle a
small set of failure reasons, the sync runner the ``SKIPPED`` / ``CANCELLED``
outcomes); turning that predicate into a :class:`Severity` under the active
:class:`Strictness` happens once, in :func:`classify_severity`.

How a severity is *shown* (symbols, colors) is presentation, left to the
CLI layer (``severity_symbol`` / ``severity_style``).
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


class Severity(str, enum.Enum):
    """Three-way classification of a finding.

    ``OK``; ``WARNING`` (non-fatal under the current policy); ``ERROR``
    (fatal).
    """

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


def classify_severity(is_inactive: bool, strictness: Strictness) -> Severity:
    """Apply the strictness policy to an "is this inactive?" boolean.

    Each domain owns the predicate, but the strictness match-case lives
    here so the policy change point isn't scattered across modules.
    """
    match strictness:
        case Strictness.IGNORE_NONE:
            return Severity.ERROR
        case Strictness.IGNORE_ALL:
            return Severity.WARNING
        case Strictness.IGNORE_INACTIVE:
            return Severity.WARNING if is_inactive else Severity.ERROR
