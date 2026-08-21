"""Rich progress bar for disk mount/umount operations."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)
from rich.text import Text

from ....clihelpers import Severity, severity_style, severity_symbol

# Result-line formatters for the mount/umount bars.  They live here, next to
# the bar that calls them, rather than in each caller: ``disks mount`` /
# ``disks umount`` and the ``managed_mount`` context manager render identical
# lines, and the second copy they used to keep is how a fix to one of them
# silently misses the other.
#
# Each returns a ``Text`` rather than a markup string: *detail* and *warning*
# are external text (an exception message, udisksctl's stderr), and Rich would
# swallow any ``[...]`` in them as a style tag.


def format_mount_result(
    slug: str, severity: Severity, detail: str | None, _warning: str | None
) -> Text:
    """Format a mount result as a styled result line."""
    return Text.assemble(
        (severity_symbol(severity), severity_style(severity)),
        f" mount {slug}",
        *([f" ({detail})"] if detail else []),
    )


def format_umount_result(
    slug: str, severity: Severity, detail: str | None, warning: str | None
) -> Text:
    """Format an umount result as a styled result line."""
    return Text.assemble(
        (severity_symbol(severity), severity_style(severity)),
        f" umount {slug}",
        *([f" ({detail})"] if detail else []),
        *([(f" warning: {warning}", "yellow")] if warning else []),
    )


class DisksProgressBar:
    """Rich progress bar for mount/umount operations.

    Manages a transient progress bar that shows a spinner, description
    (current volume name), visual bar, and M/N counter.  Result lines
    are printed above the bar as each volume completes.

    Parameters
    ----------
    total:
        Number of volumes to process.
    label:
        Verb shown in the progress description (e.g. ``"Mounting"``).
    format_result:
        Callable that formats a result line given ``(slug, severity,
        detail, warning)``.  Called once per volume on completion.
    """

    def __init__(
        self,
        total: int,
        label: str,
        format_result: Callable[[str, Severity, str | None, str | None], Text],
    ) -> None:
        self._total = total
        self._label = label
        self._format_result = format_result
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def on_start(self, slug: str) -> None:
        """Call at the beginning of each volume operation."""
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(
                f"{self._label} {slug}...", total=self._total
            )
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=f"{self._label} {slug}...")

    def on_end(
        self,
        slug: str,
        severity: Severity,
        detail: str | None = None,
        warning: str | None = None,
    ) -> None:
        """Call at the end of each volume operation."""
        if self._progress is not None:
            assert self._task_id is not None
            line = self._format_result(slug, severity, detail, warning)
            self._progress.console.print(line)
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()
