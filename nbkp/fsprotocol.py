"""Filesystem protocol: sentinel names, directory layout, and snapshot types.

This module is a leaf dependency with no internal imports, so any
module can use it without introducing circular dependencies.
"""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

#: Sentinel file placed at the volume root to confirm it is mounted.
VOLUME_SENTINEL = ".nbkp-vol"

#: Sentinel file placed at a source endpoint to confirm it is ready.
SOURCE_SENTINEL = ".nbkp-src"

#: Sentinel file placed at a destination endpoint to confirm it is ready.
DESTINATION_SENTINEL = ".nbkp-dst"

#: Directory name that holds timestamped snapshots (both btrfs and hard-link).
SNAPSHOTS_DIR = "snapshots"

#: Symlink name that points to the most recent complete snapshot.
LATEST_LINK = "latest"

#: Canonical symlink target meaning "no snapshot yet".
DEVNULL_TARGET = "/dev/null"

#: Directory name used as the btrfs staging subvolume for rsync writes.
STAGING_DIR = "staging"

#: Filesystem types (as reported by ``stat -f -c %T``) that cannot hold hard links,
#: so hard-link snapshots are rejected on them.
NO_HARDLINK_FILESYSTEMS = ("vfat", "msdos", "exfat")


def format_snapshot_name(now: datetime, *, macos_local: bool = False) -> str:
    """Format a UTC timestamp as a snapshot directory name.

    Standard form uses colons (``2026-03-06T14:30:00.000Z``).
    When *macos_local* is True, colons are replaced with hyphens
    because APFS/HFS+ forbids colons in filenames.
    """
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return ts.replace(":", "-") if macos_local else ts


#: The two snapshot name forms nbkp writes, matched in full: colons
#: (``2026-03-06T14:30:00.000Z``) or, on macOS local volumes, hyphens
#: (``2026-03-06T14-30-00.000Z``).  The generated shell script filters
#: listings with the same pattern (``nbkp_snapshot_names``).
_SNAPSHOT_NAME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"T([0-9]{2})([:-])([0-9]{2})\5([0-9]{2})\.([0-9]{3})Z"
)


def parse_snapshot_name(name: str) -> datetime:
    """Parse a snapshot directory name back to an aware UTC datetime.

    Only the exact forms :func:`format_snapshot_name` produces are
    accepted; anything else (``2026-01-01T00``, a naive timestamp, an
    offset other than ``Z``) raises :class:`ValueError`, so a directory
    nbkp did not create is never mistaken for a snapshot.
    """
    match = _SNAPSHOT_NAME.fullmatch(name)
    if match is None:
        msg = f"not a snapshot name: {name!r}"
        raise ValueError(msg)
    year, month, day, hour, _, minute, second, millis = match.groups()
    return datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second),
        int(millis) * 1000,
        tzinfo=UTC,
    )


class Snapshot(BaseModel):
    """A point-in-time snapshot identified by its filesystem directory name."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Filesystem folder name (e.g. ``2026-03-06T14:30:00.000Z`` or
    ``2026-03-06T14-30-00.000Z`` on macOS local volumes)."""
    timestamp: datetime
    """Parsed UTC datetime."""

    @staticmethod
    def create(now: datetime, *, macos_local: bool = False) -> Snapshot:
        """Create a Snapshot from a UTC timestamp."""
        return Snapshot(
            name=format_snapshot_name(now, macos_local=macos_local),
            timestamp=now,
        )

    @staticmethod
    def from_name(name: str) -> Snapshot:
        """Parse a bare snapshot directory name into a Snapshot."""
        return Snapshot(name=name, timestamp=parse_snapshot_name(name))

    @staticmethod
    def from_path(path: str) -> Snapshot:
        return Snapshot.from_name(path.rsplit("/", 1)[-1])
