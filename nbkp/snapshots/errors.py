"""Structured snapshot operation errors.

Every snapshot backend failure — a non-zero exit status, a local
``OSError``, or an SSH transport error — surfaces as a single
:class:`SnapshotOperationError`, so callers catch one type and can
inspect *what* failed (``op``), *where* (``path``), and *why*
(``stderr``) without parsing a message string.
"""

import enum
import subprocess
from collections.abc import Callable

from ..config import Volume
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoints
from ..remote.errors import ssh_connection_errors


class SnapshotOp(enum.StrEnum):
    CREATE = "create"
    MKDIR = "mkdir"
    MAKE_WRITABLE = "make-writable"
    DELETE = "delete"
    LIST = "list"
    READ_LATEST = "read-latest"
    UPDATE_LATEST = "update-latest"


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


def run_snapshot_command(
    op: SnapshotOp,
    path: str,
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* on *volume*, translating transport errors.

    The exit status is left to the caller, since some commands (``test``,
    ``ls``) use a non-zero status as an answer rather than a failure.
    """
    try:
        return run(cmd, volume, resolved_endpoints)
    except ssh_connection_errors() as e:
        raise SnapshotOperationError(op, path, str(e)) from e


def run_checked(
    op: SnapshotOp,
    path: str,
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_snapshot_command`, but a non-zero exit status fails."""
    result = run_snapshot_command(op, path, cmd, volume, resolved_endpoints, run=run)
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
