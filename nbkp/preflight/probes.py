"""
Filesystem and tool probes run on a volume's host, locally or over SSH.

Each takes the command runner as *run*, so tests can inject a fake one.
"""

import re as regex
from pathlib import Path

from nbkp.config import LocalVolume, RemoteVolume, Volume, resolve_endpoint_path
from nbkp.fsprotocol import Snapshot, parse_snapshot_listing
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


def check_endpoint_sentinel(
    volume: Volume,
    subdir: str | None,
    sentinel_name: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """Return whether the file *sentinel_name* exists in the endpoint's directory."""
    base = resolve_endpoint_path(volume, subdir)
    rel_path = f"{base}/{sentinel_name}"
    match volume:
        case LocalVolume():
            return Path(rel_path).is_file()
        case RemoteVolume():
            return (
                run(["test", "-f", rel_path], volume, resolved_endpoints).returncode
                == 0
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


def check_symlink_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """Return whether *path* on *volume* is a symlink, dangling or not."""
    match volume:
        case LocalVolume():
            return Path(path).is_symlink()
        case RemoteVolume():
            return run(["test", "-L", path], volume, resolved_endpoints).returncode == 0


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


def read_symlink_target(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> str | None:
    """Read a symlink target, returning None if it is absent or unreadable."""
    try:
        return read_symlink(volume, path, resolved_endpoints, run=run)
    except VolumeReadError:
        return None


_MIN_RSYNC_VERSION = (3, 0, 0)

_GNU_RSYNC_RE = regex.compile(r"rsync\s+version\s+(\d+)\.(\d+)\.(\d+)")


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
