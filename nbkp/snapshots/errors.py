"""
Structured snapshot operation errors.

Every snapshot backend failure — a non-zero exit status, a local
``OSError``, or an SSH transport error — surfaces as a single
:class:`SnapshotOperationError`, so callers catch one type and can
inspect *what* failed (``op``), *where* (``path``), and *why*
(``stderr``) without parsing a message string.
"""

import enum
import subprocess
from collections.abc import Callable

from nbkp.config import Volume
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import ssh_connection_errors


class SnapshotOp(enum.StrEnum):
    """Which snapshot operation failed, carried by :class:`SnapshotOperationError`."""

    CREATE = "create"
    MKDIR = "mkdir"
    MAKE_WRITABLE = "make-writable"
    MAKE_READONLY = "make-readonly"
    DELETE = "delete"
    LIST = "list"
    READ_LATEST = "read-latest"
    UPDATE_LATEST = "update-latest"


class SnapshotOperationError(Exception):
    """
    A snapshot operation failed on the destination volume.

    Not a dataclass: Python assigns ``__traceback__`` on raise, which a
    frozen dataclass forbids.  The attributes are set once here and
    never reassigned.
    """

    def __init__(self, op: SnapshotOp, path: str, stderr: str) -> None:
        """Build the error from the failed operation, its path and its stderr."""
        self.op = op
        self.path = path
        self.stderr = stderr
        super().__init__(f"{op.value} failed for {path}: {stderr.strip()}")


class SnapshotPruneError(SnapshotOperationError):
    """
    A deletion failed partway through pruning.

    ``deleted`` holds the snapshots removed before the failure, oldest
    first, so callers can still report them.
    """

    def __init__(self, cause: SnapshotOperationError, deleted: tuple[str, ...]) -> None:
        """Build the error from the failure and the snapshots deleted before it."""
        super().__init__(cause.op, cause.path, cause.stderr)
        self.deleted = deleted


def run_snapshot_command(
    op: SnapshotOp,
    path: str,
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> subprocess.CompletedProcess[str]:
    """
    Run *cmd* on *volume*, translating transport errors.

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
