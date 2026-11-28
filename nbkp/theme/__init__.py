"""
The visual vocabulary every renderer shares.

Severity symbols and styles, and the dry-run labels.

A leaf below the ``output`` modules, which import it, so a symbol or a label
is defined once. It holds no command-line knowledge: that is ``cliwiring``'s.
"""

from nbkp.theme.dryrun import (
    DRY_RUN_LABEL as DRY_RUN_LABEL,
    dry_run_labeled as dry_run_labeled,
    dry_run_table_title as dry_run_table_title,
)
from nbkp.theme.severity import (
    OK_STYLE as OK_STYLE,
    OK_SYMBOL as OK_SYMBOL,
    severity_icon as severity_icon,
    severity_style as severity_style,
    severity_symbol as severity_symbol,
)
