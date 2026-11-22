"""One nbkp command at a time touches the drives and destinations of a machine.

Two runs writing the same destination at once corrupt it: the second's
orphan cleanup deletes the snapshot the first is writing, two btrfs syncs
share one ``staging/``, and the second's umount pulls a drive from under the
first.  Any other command that mounts or umounts a drive (``disks mount``,
``preflight check``, ``snapshots show``, …) or deletes snapshots
(``snapshots prune``) can do the same to a run, or a run to it.  Each of them
therefore holds an exclusive ``flock(2)`` on one lock file per user while it
mounts, works and umounts, and the generated script takes the same lock with
``flock(1)``.  The file keeps the name ``run.lock`` the script also uses.
"""

import enum
import fcntl
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path

import typer

from ..clidisplay import OutputFormat, echo_json

_APP = "nbkp"
_LOCK_FILENAME = "run.lock"


def resolve_run_lock_path(environ: Mapping[str, str], home: Path) -> Path:
    """``$XDG_STATE_HOME/nbkp/run.lock``, else ``~/.local/state/nbkp/run.lock``.

    As for ``$XDG_CONFIG_HOME``, the XDG spec says an empty or relative
    ``$XDG_STATE_HOME`` is invalid and must be ignored.
    """
    xdg = Path(environ.get("XDG_STATE_HOME", ""))
    state_home = xdg if xdg.is_absolute() else home / ".local" / "state"
    return state_home / _APP / _LOCK_FILENAME


def is_run_lock_needed(*, writes: bool, mount: bool, umount: bool) -> bool:
    """A command that neither writes to a destination, mounts nor umounts
    touches nothing another command depends on, so it may run alongside one;
    any other takes the lock."""
    return writes or mount or umount


class RunLockErrorKind(enum.StrEnum):
    RUN_IN_PROGRESS = "run-in-progress"
    """Another process holds the lock: a run, a mount, a prune, …"""
    LOCK_UNAVAILABLE = "run-lock-unavailable"
    """The lock file could not be created, opened or locked."""


class RunLockError(OSError):
    def __init__(self, kind: RunLockErrorKind, path: Path, detail: str = "") -> None:
        super().__init__(f"{kind.value}: {path}{f': {detail}' if detail else ''}")
        self.kind = kind
        self.path = path
        self.detail = detail


type RunLockFn = Callable[[Path], AbstractContextManager[None]]


@contextmanager
def hold_run_lock(path: Path) -> Generator[None]:
    """Hold the run lock at *path* until the block exits, without waiting.

    Raises :class:`RunLockError` (``RUN_IN_PROGRESS``) when another process
    holds it, and (``LOCK_UNAVAILABLE``) when the file cannot be created or
    locked: a command that cannot tell whether another is going must not
    start.  The lock goes with the file descriptor, so a command that dies
    releases it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise RunLockError(
            RunLockErrorKind.LOCK_UNAVAILABLE, path, e.strerror or str(e)
        ) from e
    try:
        _lock_or_raise(fd, path)
        yield
    finally:
        os.close(fd)


def _lock_or_raise(fd: int, path: Path) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        raise RunLockError(RunLockErrorKind.RUN_IN_PROGRESS, path) from e
    except OSError as e:
        raise RunLockError(
            RunLockErrorKind.LOCK_UNAVAILABLE, path, e.strerror or str(e)
        ) from e


@contextmanager
def run_lock_or_exit(
    lock: RunLockFn, path: Path, output: OutputFormat, *, needed: bool = True
) -> Generator[None]:
    """Hold *lock* at *path*, when *needed*, for the block, or exit 1 before
    the block starts when another command holds it.

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
                    }
                }
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
