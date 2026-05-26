"""Severity levels for CLI status output.

Three-way classification used across check/status/progress displays:
``OK`` (green ✓), ``WARNING`` (orange ⚠, non-fatal under current
policy), and ``ERROR`` (red ✗, fatal).

Each domain (preflight errors, mount lifecycle results, sync outcomes)
decides what counts as "inactive" for itself — the shared policy that
turns ``(is_inactive, strictness)`` into a ``Severity`` lives here in
``classify_severity``, so the strictness match-case isn't duplicated
across modules.
"""

from __future__ import annotations

import enum

from .strictness import Strictness

OK_SYMBOL = "✓"  # ✓
WARNING_SYMBOL = "⚠"  # ⚠
ERROR_SYMBOL = "✗"  # ✗

OK_STYLE = "green"
WARNING_STYLE = "dark_orange"
ERROR_STYLE = "red"


class Severity(str, enum.Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


def severity_symbol(severity: Severity) -> str:
    """Bare symbol (no Rich markup) for a severity level."""
    match severity:
        case Severity.OK:
            return OK_SYMBOL
        case Severity.WARNING:
            return WARNING_SYMBOL
        case Severity.ERROR:
            return ERROR_SYMBOL


def severity_style(severity: Severity) -> str:
    """Rich style name for a severity level."""
    match severity:
        case Severity.OK:
            return OK_STYLE
        case Severity.WARNING:
            return WARNING_STYLE
        case Severity.ERROR:
            return ERROR_STYLE


def severity_icon(severity: Severity) -> str:
    """Rich-markup icon (symbol wrapped in colored markup)."""
    style = severity_style(severity)
    symbol = severity_symbol(severity)
    return f"[{style}]{symbol}[/{style}]"


def classify_severity(is_inactive: bool, strictness: Strictness) -> Severity:
    """Apply the strictness policy to an "is this inactive?" boolean.

    Each domain owns the predicate (preflight uses its
    ``INACTIVE_*_ERRORS`` frozensets, mount lifecycle uses a small
    set of failure reasons, sync runner uses ``SKIPPED`` / ``CANCELLED``
    outcomes), but the strictness match-case lives here so the policy
    change point isn't scattered across modules.
    """
    match strictness:
        case Strictness.IGNORE_NONE:
            return Severity.ERROR
        case Strictness.IGNORE_ALL:
            return Severity.WARNING
        case Strictness.IGNORE_INACTIVE:
            return Severity.WARNING if is_inactive else Severity.ERROR
