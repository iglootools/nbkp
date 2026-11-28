"""
Hold the run lock (:mod:`nbkp.runlock`) for a command, or exit when it cannot.

What the lock protects, and where it lives, is defined in :mod:`nbkp.runlock`;
this module adds only what a command prints when another holds it.
"""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import typer

from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.runlock import RunLockError, RunLockErrorKind, RunLockFn


@contextmanager
def run_lock_or_exit(
    lock: RunLockFn,
    path: Path,
    output: OutputFormat,
    *,
    needed: bool = True,
) -> Generator[None]:
    """
    Hold *lock* at *path*, when *needed*, for the block.

    Exit 1 before the block starts when another command holds it.

    *lock* and *path* are the runtime's :attr:`~.runtime.Runtime.run_lock`
    and :meth:`~.runtime.Runtime.run_lock_path`; they are passed in because
    the runtime module imports this one.
    """
    with ExitStack() as stack:
        if needed:
            try:
                stack.enter_context(lock(path))
            except RunLockError as e:
                _print_run_lock_error(e, output)
                raise typer.Exit(1) from None
        yield


def _print_run_lock_error(e: RunLockError, output: OutputFormat) -> None:
    message = _format_run_lock_error(e)
    match output:
        case OutputFormat.JSON:
            echo_json(
                {
                    "error": {
                        "reason": e.kind.value,
                        "message": message,
                        "lock_path": str(e.path),
                    },
                },
            )
        case OutputFormat.HUMAN:
            typer.echo(f"Error: {message}", err=True)


def _format_run_lock_error(e: RunLockError) -> str:
    match e.kind:
        case RunLockErrorKind.RUN_IN_PROGRESS:
            return (
                "another nbkp command (a run, a mount, a prune, …) is in"
                f" progress (it holds {e.path}). Wait for it to finish,"
                " then try again."
            )
        case RunLockErrorKind.LOCK_UNAVAILABLE:
            return (
                f"cannot take the run lock {e.path}: {e.detail}."
                " Make its directory writable, or point XDG_STATE_HOME"
                " at a writable directory."
            )
