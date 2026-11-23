"""
Filesystem protocol: sentinel names, directory layout, and snapshot types.

This module is a leaf dependency with no internal imports, so any
module can use it without introducing circular dependencies.
"""

import re
from datetime import UTC, datetime, timedelta

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
    """
    Format a UTC timestamp as a snapshot directory name.

    Standard form uses colons (``2026-03-06T14:30:00.000Z``).
    When *macos_local* is True, colons are replaced with hyphens
    because APFS/HFS+ forbids colons in filenames.

    Raises :class:`ValueError` when *now* is naive or not at UTC: its name
    would lack the ``Z`` that :func:`parse_snapshot_name` requires, and the
    snapshot would be invisible to pruning and orphan cleanup.
    """
    if now.utcoffset() != timedelta(0):
        msg = f"snapshot time must be UTC-aware, got {now.isoformat()}"
        raise ValueError(msg)
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return ts.replace(":", "-") if macos_local else ts


#: The two snapshot name forms nbkp writes, matched in full: colons
#: (``2026-03-06T14:30:00.000Z``) or, on macOS local volumes, hyphens
#: (``2026-03-06T14-30-00.000Z``).  The generated shell script filters
#: listings with the same pattern (``nbkp_snapshot_names``).
_SNAPSHOT_NAME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"T([0-9]{2})([:-])([0-9]{2})\5([0-9]{2})\.([0-9]{3})Z",
)


class InvalidSnapshotNameError(ValueError):
    """*name* is not a snapshot directory name nbkp creates."""

    def __init__(self, name: str) -> None:
        """Build the error from the rejected *name*."""
        super().__init__(f"not a snapshot name: {name!r}")
        self.name = name


def parse_snapshot_name(name: str) -> datetime:
    """
    Parse a snapshot directory name back to an aware UTC datetime.

    Only the exact forms :func:`format_snapshot_name` produces are
    accepted; anything else (``2026-01-01T00``, a naive timestamp, an
    offset other than ``Z``) raises :class:`InvalidSnapshotNameError`, a
    :class:`ValueError`, so a directory
    nbkp did not create is never mistaken for a snapshot.
    """
    match = _SNAPSHOT_NAME.fullmatch(name)
    if match is None:
        raise InvalidSnapshotNameError(name)
    year, month, day, hour, _, minute, second, millis = match.groups()
    try:
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
    except ValueError as e:
        # A well-formed name naming a date that does not exist (month 13).
        raise InvalidSnapshotNameError(name) from e


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
        """
        Create a Snapshot from a UTC timestamp.

        Its timestamp is the one its name holds, truncated to the
        millisecond, so it equals the timestamp of the same snapshot listed
        back from disk.
        """
        return Snapshot.from_name(format_snapshot_name(now, macos_local=macos_local))

    @staticmethod
    def from_name(name: str) -> Snapshot:
        """Parse a bare snapshot directory name into a Snapshot."""
        return Snapshot(name=name, timestamp=parse_snapshot_name(name))

    @staticmethod
    def from_path(path: str) -> Snapshot:
        """
        Parse the last component of a snapshot *path* into a Snapshot.

        Raises :class:`InvalidSnapshotNameError` when it is not a snapshot name.
        """
        return Snapshot.from_name(path.rsplit("/", 1)[-1])

    @staticmethod
    def from_latest_target(target: str) -> Snapshot:
        """
        Return the snapshot a ``latest`` symlink *target* designates.

        Only ``snapshots/<name>``, as nbkp writes it, qualifies: a hand-edited
        target whose last component merely looks like a timestamp
        (``../other/snapshots/<name>``) would make downstream syncs read
        another tree, and pruning or orphan cleanup key off a snapshot that
        is not this endpoint's.  Anything else raises :class:`ValueError`.
        """
        snapshot = Snapshot.from_path(target)
        if target != f"{SNAPSHOTS_DIR}/{snapshot.name}":
            msg = f"not a target under {SNAPSHOTS_DIR}/: {target!r}"
            raise ValueError(msg)
        return snapshot


def _parse_snapshot_name(name: str) -> Snapshot | None:
    try:
        return Snapshot.from_name(name)
    except ValueError:
        return None


def parse_snapshot_listing(stdout: str) -> list[Snapshot]:
    """
    Parse ``ls`` output into snapshots sorted oldest-first.

    Entries that are not snapshot timestamps (``lost+found`` at the root
    of a dedicated filesystem, ``.DS_Store``, a stray file an operator
    left behind) are ignored rather than treated as errors: nbkp never
    created them, so it neither counts nor prunes them.  The generated
    shell script filters the same way (``nbkp_snapshot_names``).

    Sorted by time, not by name: a destination written from both Linux
    (``10:15``) and macOS (``10-30``) holds both name forms, and ``:``
    sorts after ``-``, so name order would put the older one last.
    """
    snapshots = [
        snapshot
        for line in stdout.splitlines()
        for snapshot in [_parse_snapshot_name(line.strip())]
        if snapshot is not None
    ]
    return sorted(snapshots, key=lambda s: (s.timestamp, s.name))
