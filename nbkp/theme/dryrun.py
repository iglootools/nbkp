"""
The label that marks the human output of a dry run.

A line describing an action that would be taken starts with it
(``[dry-run] Would unlock, mount enc``); a table or summary carries it once,
at the start of its title (``[dry-run] Sync results:``). JSON output carries
a top-level ``"dry_run": true`` field instead.
"""

from rich.text import Text

DRY_RUN_LABEL = "[dry-run]"
"""Rich parses it as a style tag inside a markup string, so it is only ever
rendered through :class:`~rich.text.Text`, which never parses its content."""


def dry_run_labeled(line: str | Text) -> Text:
    """*line* starting with :data:`DRY_RUN_LABEL`, keeping *line*'s styles."""
    return Text.assemble(DRY_RUN_LABEL, " ", line)


def dry_run_table_title(title: str, *, dry_run: bool) -> Text:
    """
    Return a table title, labeled when *dry_run*.

    Styled as Rich styles a ``str`` title, which it does not do for a
    :class:`~rich.text.Text` one.
    """
    return Text(f"{DRY_RUN_LABEL} {title}" if dry_run else title, style="table.title")
