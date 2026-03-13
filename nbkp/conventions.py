"""Filesystem naming conventions shared across the application.

This module is a leaf dependency with no internal imports, so any
module can use it without introducing circular dependencies.
"""

from __future__ import annotations

import re
from datetime import datetime

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


def format_snapshot_name(now: datetime, *, macos_local: bool = False) -> str:
    """Format a UTC timestamp as a snapshot directory name.

    Standard form uses colons (``2026-03-06T14:30:00.000Z``).
    When *macos_local* is True, colons are replaced with hyphens
    because APFS/HFS+ forbids colons in filenames.
    """
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return ts.replace(":", "-") if macos_local else ts


def parse_snapshot_name(name: str) -> datetime:
    """Parse a snapshot directory name back to a UTC datetime.

    Handles both standard colons (``2026-03-06T14:30:00.000Z``)
    and macOS hyphens (``2026-03-06T14-30-00.000Z``).
    """
    normalized = re.sub(r"T(\d{2})-(\d{2})-(\d{2})", r"T\1:\2:\3", name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


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
        """Extract the snapshot name from a full path and parse it."""
        return Snapshot.from_name(path.rsplit("/", 1)[-1])
