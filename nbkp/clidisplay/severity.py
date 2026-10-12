"""How a :class:`~nbkp.policy.Severity` is displayed.

``OK`` (green ✓), ``WARNING`` (orange ⚠), ``ERROR`` (red ✗).  Deciding
which severity a finding has is policy (:mod:`nbkp.policy`); this module
only maps it to symbols and Rich styles.
"""

from __future__ import annotations

from ..policy import Severity

OK_SYMBOL = "✓"  # ✓
WARNING_SYMBOL = "⚠"  # ⚠
ERROR_SYMBOL = "✗"  # ✗

OK_STYLE = "green"
WARNING_STYLE = "dark_orange"
ERROR_STYLE = "red"


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
