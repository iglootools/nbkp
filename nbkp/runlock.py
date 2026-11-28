"""
One nbkp command at a time touches the drives and destinations of a machine.

Two runs writing the same destination at once corrupt it: the second's
orphan cleanup deletes the snapshot the first is writing, two btrfs syncs
share one ``staging/``, and the second's umount pulls a drive from under the
first.  Any other command that mounts or umounts a drive (``disks mount``,
``preflight check``, ``snapshots show``, …) or deletes snapshots
(``snapshots prune``) can do the same to a run, or a run to it.  Each of them
therefore holds an exclusive ``flock(2)`` on one lock file per user while it
mounts, works and umounts, and the script ``sh`` generates takes the same lock
with ``flock(1)``.

The lock is a protocol between ``nbkp`` and that script, so its location is
defined here, below both: the script renders its path from
:data:`RUN_LOCK_STATE_SUBPATH`.
"""

import enum
import fcntl
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from nbkp.config.xdg import resolve_xdg_dir

#: The lock file, relative to the XDG state directory (``$XDG_STATE_HOME``, else
#: ``~/.local/state``). The generated script renders the same path.
RUN_LOCK_STATE_SUBPATH = "nbkp/run.lock"


def resolve_run_lock_path(environ: Mapping[str, str], home: Path) -> Path:
    """
    Return ``$XDG_STATE_HOME/nbkp/run.lock``, else ``~/.local/state/nbkp/run.lock``.

    An empty or relative ``$XDG_STATE_HOME`` is ignored, as the XDG spec asks.
    """
    state_home = resolve_xdg_dir(environ, "XDG_STATE_HOME", home / ".local" / "state")
    return state_home / RUN_LOCK_STATE_SUBPATH


def is_run_lock_needed(*, writes: bool, mounts_drives: bool) -> bool:
    """
    Return whether a command must take the run lock.

    *mounts_drives* is whether the command will mount (and so later umount) a drive:
    mounting is on and a mount-managed volume is in scope. A command that neither
    writes to a destination nor mounts touches nothing another command depends on,
    so it may run alongside one, even with ``--mount`` on; any other takes the lock.
    """
    return writes or mounts_drives


class RunLockErrorKind(enum.StrEnum):
    """Why the run lock could not be taken."""

    RUN_IN_PROGRESS = "run-in-progress"
    """Another process holds the lock: a run, a mount, a prune, …"""
    LOCK_UNAVAILABLE = "run-lock-unavailable"
    """The lock file could not be created, opened or locked."""


class RunLockError(OSError):
    """
    The run lock could not be taken, so the command must not start.

    ``kind`` says why, ``path`` is the lock file, and ``detail`` holds the OS
    error message when the file could not be created, opened or locked.
    """

    def __init__(self, kind: RunLockErrorKind, path: Path, detail: str = "") -> None:
        """Build the error from its kind, the lock file and an optional detail."""
        super().__init__(f"{kind.value}: {path}{f': {detail}' if detail else ''}")
        self.kind = kind
        self.path = path
        self.detail = detail


type RunLockFn = Callable[[Path], AbstractContextManager[None]]


@contextmanager
def hold_run_lock(path: Path) -> Generator[None]:
    """
    Hold the run lock at *path* until the block exits, without waiting.

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
            RunLockErrorKind.LOCK_UNAVAILABLE,
            path,
            e.strerror or str(e),
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
            RunLockErrorKind.LOCK_UNAVAILABLE,
            path,
            e.strerror or str(e),
        ) from e
