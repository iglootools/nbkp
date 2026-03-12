"""Filesystem naming conventions shared across the application.

This module is a leaf dependency with no internal imports, so any
module can use it without introducing circular dependencies.
"""

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
