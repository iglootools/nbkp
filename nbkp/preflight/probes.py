"""
Filesystem and tool probes run on a volume's host, locally or over SSH.

Each takes the command runner as *run*, so tests can inject a fake one.
"""

import re
import stat
from pathlib import Path

from nbkp.config import LocalVolume, RemoteVolume, Volume, resolve_endpoint_path
from nbkp.fsprotocol import (
    NEAREST_DIR_SEARCHABLE_SCRIPT,
    Snapshot,
    parse_snapshot_listing,
)
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints


class VolumeReadError(Exception):
    """
    A path on a volume exists but could not be read.

    Raised by the shared reads below; each caller turns it into its own error
    policy (``snapshots`` raises, troubleshoot shows a placeholder).
    """

    def __init__(self, path: str, detail: str) -> None:
        """Build the error from the path and what the failed command said."""
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def check_file_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """
    Return whether the regular file *path* exists on *volume*.

    A file behind a directory the user cannot search may or may not exist:
    that raises :class:`VolumeReadError`, so "cannot tell" is never mistaken
    for "absent". Transport errors propagate.
    """
    match volume:
        case LocalVolume():
            return _local_file_exists(path)
        case RemoteVolume():
            return _remote_file_exists(volume, path, resolved_endpoints, run)


def _remote_file_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> bool:
    if run(["test", "-f", path], volume, resolved_endpoints).returncode == 0:
        return True
    else:
        nearest_dir = run(
            ["sh", "-c", NEAREST_DIR_SEARCHABLE_SCRIPT, "sh", path],
            volume,
            resolved_endpoints,
        )
        match nearest_dir.returncode:
            case 0:
                return False
            case _:
                raise VolumeReadError(path, _unsearchable_detail(nearest_dir.stderr))


def _local_file_exists(path: str) -> bool:
    try:
        return stat.S_ISREG(Path(path).stat().st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError as e:
        raise VolumeReadError(path, e.strerror or str(e)) from e


def _unsearchable_detail(stderr: str | None) -> str:
    directory = (stderr or "").strip()
    return f"{directory} is not searchable" if directory else "cannot tell"


def check_endpoint_sentinel(
    volume: Volume,
    subdir: str | None,
    sentinel_name: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """
    Return whether the file *sentinel_name* exists in the endpoint's directory.

    Raises :class:`VolumeReadError` when that cannot be told (see
    :func:`check_file_exists`).
    """
    base = resolve_endpoint_path(volume, subdir)
    return check_file_exists(
        volume,
        f"{base}/{sentinel_name}",
        resolved_endpoints,
        run=run,
    )


def check_directory_writable(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """Return whether *path* on *volume* is writable by the user nbkp runs as."""
    return run(["test", "-w", path], volume, resolved_endpoints).returncode == 0


def check_directory_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """Return whether *path* on *volume* is a directory."""
    match volume:
        case LocalVolume():
            return Path(path).is_dir()
        case RemoteVolume():
            return run(["test", "-d", path], volume, resolved_endpoints).returncode == 0


def list_snapshot_dir(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> list[Snapshot]:
    """
    List the snapshots in the ``snapshots/`` directory *path*, oldest first.

    ``[]`` when the directory is absent. A directory that exists but cannot be
    listed raises :class:`VolumeReadError`; transport errors propagate.
    """
    listing = run(["ls", path], volume, resolved_endpoints)
    if listing.returncode == 0:
        return parse_snapshot_listing(listing.stdout)
    else:
        exists = run(["test", "-d", path], volume, resolved_endpoints)
        match exists.returncode:
            case 1:
                return []
            case 0:
                raise VolumeReadError(path, listing.stderr or "")
            case _:
                raise VolumeReadError(path, exists.stderr or "")


def _read_remote_symlink(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    run: VolumeCommandRunner,
) -> str | None:
    """``readlink``; on failure, ``test -L`` tells "absent" from "unreadable"."""
    result = run(["readlink", path], volume, resolved_endpoints)
    if result.returncode == 0:
        return result.stdout.strip()
    elif run(["test", "-L", path], volume, resolved_endpoints).returncode == 1:
        return None
    else:
        raise VolumeReadError(path, result.stderr or "")


def read_symlink(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> str | None:
    """
    Read the raw target of the symlink *path*, or ``None`` if there is none.

    A remote symlink that exists but cannot be read raises
    :class:`VolumeReadError`, so "unknown" is never mistaken for "absent"; a
    local filesystem error propagates as :class:`OSError`, and so do transport
    errors.
    """
    match volume:
        case LocalVolume():
            p = Path(path)
            return str(p.readlink()) if p.is_symlink() else None
        case RemoteVolume():
            return _read_remote_symlink(volume, path, resolved_endpoints, run)


_MIN_RSYNC_VERSION = (3, 0, 0)

_GNU_RSYNC_RE = re.compile(r"rsync\s+version\s+(\d+)\.(\d+)\.(\d+)")


def parse_rsync_version(output: str) -> tuple[int, ...]:
    """
    Extract version tuple from ``rsync --version`` output.

    GNU rsync:  ``rsync  version 3.2.7  protocol version 31``
    openrsync:  ``openrsync: protocol version 29``

    Returns ``(0, 0, 0)`` for openrsync or unparseable output.
    """
    m = _GNU_RSYNC_RE.search(output) if "openrsync" not in output else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def check_rsync_version(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """
    Return whether *volume*'s host has rsync 3.0.0 or later.

    openrsync, and output :func:`parse_rsync_version` cannot parse, count as too old.
    """
    result = run(["rsync", "--version"], volume, resolved_endpoints)
    return (
        result.returncode == 0
        and parse_rsync_version(result.stdout) >= _MIN_RSYNC_VERSION
    )
