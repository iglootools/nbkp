"""Generic Rich progress bar for step-by-step operations."""

from __future__ import annotations

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)


class StepProgressBar:
    """Rich progress bar for step-by-step check operations.

    Shows a spinner, description (current step label), visual bar,
    and M/N counter.  Result lines are printed above the bar
    as each step completes.

    Parameters
    ----------
    total:
        Number of steps to perform.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def on_start(self, label: str) -> None:
        """Call before each step begins."""
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
                f"Checking {label}...", total=self._total
            )
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=f"Checking {label}...")

    def on_end(
        self,
        label: str,
        active: bool,
        error_summary: str | None = None,
    ) -> None:
        """Call after each step completes."""
        if self._progress is not None:
            assert self._task_id is not None
            icon = "[green]\u2713[/green]" if active else "[red]\u2717[/red]"
            detail = f" ({error_summary})" if error_summary else ""
            self._progress.console.print(f"{icon} check {label}{detail}")
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()
