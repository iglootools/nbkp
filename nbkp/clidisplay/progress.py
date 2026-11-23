"""A step progress bar that prints each step's result line above it."""

from collections.abc import Generator
from contextlib import contextmanager
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

from nbkp.clidisplay.severity import severity_style, severity_symbol
from nbkp.policy import Severity


class StepProgressBar:
    """
    Spinner, current step label, bar and M/N counter.

    Result lines are printed above the bar as each step completes.
    """

    def __init__(self, total: int) -> None:
        """Build the bar for *total* steps; it appears at the first step."""
        self._total = total
        # The bar and its task, from the first step on.
        self._bar: tuple[Progress, TaskID] | None = None

    def on_start(self, label: str) -> None:
        """
        Call before each step begins.

        *label* is the in-progress description shown next to the
        spinner (e.g. ``"Checking nas-server..."``).
        """
        if self._bar is None:
            progress = Progress(
                SpinnerColumn(),
                # Descriptions embed slugs and paths: render them literally,
                # not as Rich markup.
                TextColumn(
                    "{task.description}",
                    style="progress.description",
                    markup=False,
                ),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
            )
            progress.start()
            self._bar = (progress, progress.add_task(label, total=self._total))
        else:
            progress, task_id = self._bar
            progress.update(task_id, description=label)

    def on_end(
        self,
        label: str,
        severity: Severity,
        detail: str | None = None,
        warning: str | None = None,
    ) -> None:
        """
        Call after each step completes.

        *label* is the result line printed above the bar
        (e.g. ``"check nas-server"``), followed by *detail* in parentheses
        and *warning* in yellow (what a failed step leaves behind).

        The line is assembled as a :class:`~rich.text.Text` rather than a
        markup string because *detail* carries external text — an exception
        message or a command's stderr.  Rich would eat any ``[...]`` in it
        as a style tag (``pip install nbkp[keyring]`` renders as
        ``pip install nbkp``); ``Text`` never parses its content as markup.
        """
        if self._bar is not None:
            progress, task_id = self._bar
            progress.console.print(
                Text.assemble(
                    (severity_symbol(severity), severity_style(severity)),
                    f" {label}",
                    *([f" ({detail})"] if detail else []),
                    *([(f" warning: {warning}", "yellow")] if warning else []),
                ),
            )
            progress.advance(task_id)

    @property
    def shown(self) -> bool:
        """Whether the bar is on screen: started by a step and not stopped."""
        return self._bar is not None and self._bar[0].live.is_started

    @contextmanager
    def hidden(self) -> Generator[None]:
        """
        Take the bar off screen for the block, then show it again.

        For an interactive prompt: the bar redraws its line several times a second,
        which would erase a prompt written to the terminal behind its back.  Does
        nothing when the bar is not shown.
        """
        if self._bar is None or not self.shown:
            yield
            return
        progress = self._bar[0]
        progress.stop()
        try:
            yield
        finally:
            progress.start()

    def stop(self) -> None:
        """Idempotent."""
        if self._bar is not None:
            self._bar[0].stop()

    def __enter__(self) -> Self:
        """Return the bar, which :meth:`__exit__` stops."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the bar, whether or not the block raised."""
        self.stop()
