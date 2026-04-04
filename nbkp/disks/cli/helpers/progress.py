"""Rich progress bar for disk mount/umount operations."""

from __future__ import annotations

from types import TracebackType
from typing import Callable

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
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
        Callable that formats a result line given ``(slug, success, detail,
        warning)``.  Called once per volume on completion.
    """

    def __init__(
        self,
        total: int,
        label: str,
        format_result: Callable[[str, bool, str | None, str | None], str],
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
        success: bool,
        detail: str | None = None,
        warning: str | None = None,
    ) -> None:
        """Call at the end of each volume operation."""
        if self._progress is not None:
            assert self._task_id is not None
            line = self._format_result(slug, success, detail, warning)
            self._progress.console.print(line)
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()

    def __enter__(self) -> DisksProgressBar:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()
