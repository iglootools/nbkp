"""Host-interaction primitives for pre-flight checks.

Low-level functions that run commands on local/remote hosts and
return results.  No domain knowledge about syncs or snapshots.
"""

from __future__ import annotations

import re as regex
import shutil
from pathlib import Path

from ..config import (
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    Volume,
)
from ..remote.dispatch import run_on_volume


def _check_endpoint_sentinel(
    volume: Volume,
    subdir: str | None,
    sentinel_name: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if an endpoint sentinel file exists."""
    rel_path = (
        f"{volume.path}/{subdir}/{sentinel_name}"
        if subdir
        else f"{volume.path}/{sentinel_name}"
    )
    match volume:
        case LocalVolume():
            return Path(rel_path).exists()
        case RemoteVolume():
            return (
                run_on_volume(
                    ["test", "-f", rel_path], volume, resolved_endpoints
                ).returncode
                == 0
            )


def _check_command_available(
    volume: Volume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a command is available on the volume's host."""
    match volume:
        case LocalVolume():
            return shutil.which(command) is not None
        case RemoteVolume():
            return (
                run_on_volume(["which", command], volume, resolved_endpoints).returncode
                == 0
            )


def resolve_endpoint(volume: Volume, subdir: str | None) -> str:
    """Resolve the full endpoint path for a volume."""
    return f"{volume.path}/{subdir}" if subdir else volume.path


def _check_directory_writable(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a directory is writable on the volume's host."""
    return (
        run_on_volume(["test", "-w", path], volume, resolved_endpoints).returncode == 0
    )


def check_directory_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a directory exists on the volume's host."""
    match volume:
        case LocalVolume():
            return Path(path).is_dir()
        case RemoteVolume():
            return (
                run_on_volume(
                    ["test", "-d", path], volume, resolved_endpoints
                ).returncode
                == 0
            )


def _check_symlink_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a symlink exists on the volume's host."""
    match volume:
        case LocalVolume():
            return Path(path).is_symlink()
        case RemoteVolume():
            return (
                run_on_volume(
                    ["test", "-L", path], volume, resolved_endpoints
                ).returncode
                == 0
            )


def read_symlink_target(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Read a symlink target, returning None if it doesn't exist."""
    match volume:
        case LocalVolume():
            p = Path(path)
            return str(p.readlink()) if p.is_symlink() else None
        case RemoteVolume():
            result = run_on_volume(["readlink", path], volume, resolved_endpoints)
            return result.stdout.strip() if result.returncode == 0 else None


def _check_file_exists(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a file exists on the volume's host."""
    match volume:
        case LocalVolume():
            return Path(path).is_file()
        case RemoteVolume():
            return (
                run_on_volume(
                    ["test", "-f", path], volume, resolved_endpoints
                ).returncode
                == 0
            )


def _run_systemctl_show(
    volume: Volume,
    unit: str,
    properties: list[str],
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, str]:
    """Run ``systemctl show`` for a unit and parse property=value pairs."""
    cmd = [
        "systemctl",
        "show",
        unit,
        "--no-pager",
        *[f"-p{p}" for p in properties],
    ]
    result = run_on_volume(cmd, volume, resolved_endpoints)
    if result.returncode != 0:
        return {}
    else:
        return dict(
            line.split("=", 1)
            for line in result.stdout.strip().splitlines()
            if "=" in line
        )


def _check_systemctl_cat(
    volume: Volume,
    unit: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a systemd unit is known (``systemctl cat`` succeeds)."""
    return (
        run_on_volume(["systemctl", "cat", unit], volume, resolved_endpoints).returncode
        == 0
    )


_MIN_RSYNC_VERSION = (3, 0, 0)

_GNU_RSYNC_RE = regex.compile(r"rsync\s+version\s+(\d+)\.(\d+)\.(\d+)")


def parse_rsync_version(output: str) -> tuple[int, ...]:
    """Extract version tuple from ``rsync --version`` output.

    GNU rsync:  ``rsync  version 3.2.7  protocol version 31``
    openrsync:  ``openrsync: protocol version 29``

    Returns ``(0, 0, 0)`` for openrsync or unparseable output.
    """
    m = _GNU_RSYNC_RE.search(output) if "openrsync" not in output else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def _check_rsync_version(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check that rsync is GNU rsync >= 3.0.0."""
    result = run_on_volume(["rsync", "--version"], volume, resolved_endpoints)
    return (
        result.returncode == 0
        and parse_rsync_version(result.stdout) >= _MIN_RSYNC_VERSION
    )
