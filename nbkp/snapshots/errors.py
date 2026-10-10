"""Structured snapshot operation errors.

Every snapshot backend failure — a non-zero exit status, a local
``OSError``, or an SSH transport error — surfaces as a single
:class:`SnapshotOperationError`, so callers catch one type and can
inspect *what* failed (``op``), *where* (``path``), and *why*
(``stderr``) without parsing a message string.
"""

from __future__ import annotations

import enum
import subprocess
from collections.abc import Callable

import paramiko

from ..config import Volume
from ..remote.dispatch import run_on_volume
from ..remote.endpoints import ResolvedEndpoints


class SnapshotOp(str, enum.Enum):
    """The snapshot operation that failed."""

    CREATE = "create snapshot"
    MKDIR = "create snapshot directory"
    MAKE_WRITABLE = "make snapshot writable"
    DELETE = "delete snapshot"
    LIST = "list snapshots"
    READ_LATEST = "read latest symlink"
    UPDATE_LATEST = "update latest symlink"


class SnapshotOperationError(Exception):
    """A snapshot operation failed on the destination volume.

    Not a dataclass: Python assigns ``__traceback__`` on raise, which a
    frozen dataclass forbids.  The attributes are set once here and
    never reassigned.
    """

    def __init__(self, op: SnapshotOp, path: str, stderr: str) -> None:
        self.op = op
        self.path = path
        self.stderr = stderr
        super().__init__(f"{op.value} failed for {path}: {stderr.strip()}")


# Errors raised by the transport rather than by the command itself:
# OSError covers local spawn failures, sockets, DNS (socket.gaierror) and
# Paramiko's NoValidConnectionsError; SSHException covers authentication
# and protocol failures.
TRANSPORT_ERRORS: tuple[type[Exception], ...] = (OSError, paramiko.SSHException)


def run_snapshot_command(
    op: SnapshotOp,
    path: str,
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* on *volume*, translating transport errors.

    The exit status is left to the caller, since some commands (``test``,
    ``ls``) use a non-zero status as an answer rather than a failure.
    """
    try:
        return run_on_volume(cmd, volume, resolved_endpoints)
    except TRANSPORT_ERRORS as e:
        raise SnapshotOperationError(op, path, str(e)) from e


def run_checked(
    op: SnapshotOp,
    path: str,
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_snapshot_command`, but a non-zero exit status fails."""
    result = run_snapshot_command(op, path, cmd, volume, resolved_endpoints)
    if result.returncode != 0:
        raise SnapshotOperationError(op, path, result.stderr or "")
    else:
        return result


def local_fs_op[T](op: SnapshotOp, path: str, fn: Callable[[], T]) -> T:
    """Run a local filesystem operation, translating ``OSError``."""
    try:
        return fn()
    except OSError as e:
        raise SnapshotOperationError(op, path, str(e)) from e
