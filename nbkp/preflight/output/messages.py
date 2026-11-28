"""
The sentence each preflight problem kind is shown as.

The kinds' values are stable tokens that scripts match on in JSON output;
the wording lives here, so it can change without breaking them. One table
per enum: two kinds of different enums can share a token, and a ``StrEnum``
member hashes as its value, so a single table would merge them.
"""

from collections.abc import Iterable

from nbkp.fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from nbkp.preflight.severity import PreflightErrorKind, PreflightWarningKind
from nbkp.preflight.status import (
    DestinationEndpointErrorKind,
    DestinationEndpointWarningKind,
    SourceEndpointErrorKind,
    SshEndpointErrorKind,
    SshEndpointWarningKind,
    SyncErrorKind,
    VolumeErrorKind,
)

_VOLUME: dict[VolumeErrorKind, str] = {
    VolumeErrorKind.VOLUME_NOT_MOUNTED: "volume not mounted",
    VolumeErrorKind.SENTINEL_NOT_FOUND: f"{VOLUME_SENTINEL} volume sentinel not found",
    VolumeErrorKind.SENTINEL_UNREADABLE: (
        f"cannot tell whether the {VOLUME_SENTINEL} volume sentinel exists"
    ),
    VolumeErrorKind.DEVICE_NOT_PRESENT: "device not plugged in",
    VolumeErrorKind.UNLOCK_FAILED: "failed to unlock luks encrypted device",
    VolumeErrorKind.MOUNT_FAILED: "failed to mount volume",
    VolumeErrorKind.PASSPHRASE_NOT_AVAILABLE: "passphrase not available",
    VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH: (
        "no fstab entry maps the device to the configured path"
    ),
    VolumeErrorKind.POLKIT_RULES_MISSING: "polkit rules not configured",
    VolumeErrorKind.SSH_ENDPOINT_INACTIVE: "ssh endpoint inactive",
}

_SSH_ENDPOINT: dict[SshEndpointErrorKind, str] = {
    SshEndpointErrorKind.UNREACHABLE: "unreachable",
    SshEndpointErrorKind.LOCATION_EXCLUDED: "excluded by location filter",
    SshEndpointErrorKind.AUTH_FAILED: (
        "ssh authentication or host key verification failed"
    ),
    SshEndpointErrorKind.RSYNC_NOT_FOUND: "rsync not found",
    SshEndpointErrorKind.RSYNC_TOO_OLD: "rsync too old (3.0+ required)",
    SshEndpointErrorKind.BTRFS_NOT_FOUND: "btrfs not found",
    SshEndpointErrorKind.STAT_NOT_FOUND: "stat not found",
    SshEndpointErrorKind.FINDMNT_NOT_FOUND: "findmnt not found",
    SshEndpointErrorKind.UDISKSCTL_NOT_FOUND: "udisksctl not found",
    SshEndpointErrorKind.UDISKSD_NOT_RUNNING: "udisksd (udisks2 daemon) not running",
    SshEndpointErrorKind.LSBLK_NOT_FOUND: "lsblk not found",
}

_SSH_ENDPOINT_WARNING: dict[SshEndpointWarningKind, str] = {
    SshEndpointWarningKind.UDISKS_BTRFS_MODULE_MISSING: (
        "udisks2 btrfs module not installed"
    ),
}

_DESTINATION_ENDPOINT_WARNING: dict[DestinationEndpointWarningKind, str] = {
    DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE: (
        f"the snapshot {LATEST_LINK} points to is not read-only"
    ),
}

_SYNC: dict[SyncErrorKind, str] = {
    SyncErrorKind.DISABLED: "disabled",
    SyncErrorKind.ENDPOINT_HOST_ERRORS: "a host this sync runs on has tool errors",
    SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM: (
        "source latest → /dev/null with no upstream sync in the run"
    ),
    SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING: (
        "source snapshot not yet available (dry-run; upstream has not run)"
    ),
    SyncErrorKind.SOURCE_ENDPOINT_INACTIVE: "source endpoint inactive",
    SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE: "destination endpoint inactive",
}

_SOURCE_ENDPOINT: dict[SourceEndpointErrorKind, str] = {
    SourceEndpointErrorKind.SENTINEL_NOT_FOUND: f"{SOURCE_SENTINEL} sentinel not found",
    SourceEndpointErrorKind.SENTINEL_UNREADABLE: (
        f"cannot tell whether the {SOURCE_SENTINEL} sentinel exists"
    ),
    SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND: (
        f"{SNAPSHOTS_DIR}/ directory not found"
    ),
    SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND: (
        f"{LATEST_LINK} symlink not found"
    ),
    SourceEndpointErrorKind.LATEST_SYMLINK_INVALID: (
        f"{LATEST_LINK} symlink target is invalid"
    ),
    SourceEndpointErrorKind.VOLUME_INACTIVE: "volume inactive",
}

_DESTINATION_ENDPOINT: dict[DestinationEndpointErrorKind, str] = {
    DestinationEndpointErrorKind.SENTINEL_NOT_FOUND: (
        f"{DESTINATION_SENTINEL} sentinel not found"
    ),
    DestinationEndpointErrorKind.SENTINEL_UNREADABLE: (
        f"cannot tell whether the {DESTINATION_SENTINEL} sentinel exists"
    ),
    DestinationEndpointErrorKind.NOT_WRITABLE: "endpoint directory not writable",
    DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND: (
        f"{SNAPSHOTS_DIR}/ directory not found"
    ),
    DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_WRITABLE: (
        f"{SNAPSHOTS_DIR}/ directory not writable"
    ),
    DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND: (
        f"{LATEST_LINK} symlink not found"
    ),
    DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID: (
        f"{LATEST_LINK} symlink target is invalid"
    ),
    DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND: (
        f"{STAGING_DIR}/ directory not found"
    ),
    DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME: (
        f"{STAGING_DIR}/ is not a btrfs subvolume"
    ),
    DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_WRITABLE: (
        f"{STAGING_DIR}/ subvolume not writable"
    ),
    DestinationEndpointErrorKind.VOL_NOT_BTRFS: "volume not on btrfs filesystem",
    DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN: (
        "could not determine the volume filesystem type"
    ),
    DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM: (
        "volume not mounted with user_subvol_rm_allowed"
    ),
    DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT: (
        "volume filesystem does not support hard links"
    ),
    DestinationEndpointErrorKind.VOLUME_INACTIVE: "volume inactive",
}


def format_problem(kind: PreflightErrorKind | PreflightWarningKind) -> str:
    """Return the lowercase phrase *kind* is shown as to the user."""
    match kind:
        case VolumeErrorKind():
            return _VOLUME[kind]
        case SshEndpointErrorKind():
            return _SSH_ENDPOINT[kind]
        case SshEndpointWarningKind():
            return _SSH_ENDPOINT_WARNING[kind]
        case SyncErrorKind():
            return _SYNC[kind]
        case SourceEndpointErrorKind():
            return _SOURCE_ENDPOINT[kind]
        case DestinationEndpointErrorKind():
            return _DESTINATION_ENDPOINT[kind]
        case DestinationEndpointWarningKind():
            return _DESTINATION_ENDPOINT_WARNING[kind]


def format_problems(
    kinds: Iterable[PreflightErrorKind | PreflightWarningKind],
) -> str:
    """Return the phrases of *kinds*, comma-separated, in their given order."""
    return ", ".join(format_problem(kind) for kind in kinds)
