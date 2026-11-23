"""Layer 2 — Volume: sentinel, mount config/state, filesystem properties."""

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from nbkp.config import MountConfig, RemoteVolume, Volume
from nbkp.disks import MountCapabilities, MountFailureKind
from nbkp.preflight.status.ssh import SshEndpointStatus


class VolumeErrorKind(enum.StrEnum):
    """
    Errors of a volume.

    SSH reachability and host tool availability errors are ``SshEndpointErrorKind``s,
    not volume errors.
    """

    VOLUME_NOT_MOUNTED = "volume-not-mounted"

    SENTINEL_NOT_FOUND = "sentinel-not-found"

    DEVICE_NOT_PRESENT = "device-not-present"
    UNLOCK_FAILED = "unlock-failed"
    MOUNT_FAILED = "mount-failed"
    PASSPHRASE_NOT_AVAILABLE = "passphrase-not-available"  # noqa: S105 -- an error kind, not a secret

    # fstab config: fixed-path (Option A) mounts only
    FSTAB_MOUNTPOINT_MISMATCH = "fstab-mountpoint-mismatch"

    # Auth rules (polkit only — udisks has no sudoers path)
    POLKIT_RULES_MISSING = "polkit-rules-missing"

    # Cascade — lower layer inactive
    SSH_ENDPOINT_INACTIVE = "ssh-endpoint-inactive"


INACTIVE_VOLUME_ERRORS: frozenset[VolumeErrorKind] = frozenset(
    {
        VolumeErrorKind.SENTINEL_NOT_FOUND,
        VolumeErrorKind.DEVICE_NOT_PRESENT,
        VolumeErrorKind.VOLUME_NOT_MOUNTED,
        VolumeErrorKind.SSH_ENDPOINT_INACTIVE,
    },
)


class VolumeCapabilities(BaseModel):
    """
    Volume-level capabilities, computed once per reachable volume.

    Host-level tool availability (rsync, btrfs, stat, findmnt) lives
    on ``HostToolCapabilities`` at the SSH endpoint level.  This model
    captures only volume-specific filesystem properties.
    """

    model_config = ConfigDict(frozen=True)

    sentinel_exists: bool
    is_btrfs_filesystem: bool | None
    """``None`` when the filesystem type probe failed: type unknown."""
    hardlink_supported: bool | None
    """``None`` when the filesystem type probe failed: support unknown."""
    btrfs_user_subvol_rm: bool
    mount: MountCapabilities | None = None


class VolumeDiagnostics(BaseModel):
    """
    Observed state of a volume.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``VolumeStatus.from_diagnostics`` translates these into
    ``VolumeErrorKind`` values.  SSH reachability and location exclusion
    live on ``SshEndpointDiagnostics``.
    """

    model_config = ConfigDict(frozen=True)

    capabilities: VolumeCapabilities | None = None
    """``None`` when the volume sentinel is missing and no mount config
    to probe (rare — usually at least sentinel_exists is set)."""


class VolumeStatus(BaseModel):
    """A volume's diagnostics, its SSH endpoint's status, and the errors they mean."""

    model_config = ConfigDict(frozen=True)

    slug: str
    config: Volume
    ssh_endpoint_status: SshEndpointStatus
    diagnostics: VolumeDiagnostics | None
    """``None`` when the SSH endpoint is inactive (unreachable or
    excluded)."""
    errors: list[VolumeErrorKind]

    @computed_field
    @property
    def active(self) -> bool:
        """Return whether the volume has no errors."""
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        config: Volume,
        ssh_endpoint_status: SshEndpointStatus,
        diagnostics: VolumeDiagnostics | None,
    ) -> VolumeStatus:
        """
        Build the status from the diagnostics and the SSH endpoint's status.

        Only remote volumes cascade an inactive SSH endpoint: localhost is always
        reachable, and its tool errors surface at the sync level as
        ``SyncErrorKind.ENDPOINT_HOST_ERRORS`` instead.
        """
        return VolumeStatus(
            slug=slug,
            config=config,
            ssh_endpoint_status=ssh_endpoint_status,
            diagnostics=diagnostics,
            errors=(
                [VolumeErrorKind.SSH_ENDPOINT_INACTIVE]
                if isinstance(config, RemoteVolume) and not ssh_endpoint_status.active
                else _volume_errors(diagnostics, config.mount)
                if diagnostics is not None
                else []
            ),
        )


def _volume_errors(
    diag: VolumeDiagnostics,
    mount: MountConfig | None = None,
) -> list[VolumeErrorKind]:
    match (diag.capabilities, mount):
        case (None | VolumeCapabilities(sentinel_exists=True), _):
            return []
        case (VolumeCapabilities(mount=MountCapabilities() as caps), MountConfig()):
            return [_mount_sentinel_error(caps)]
        case _:
            return [VolumeErrorKind.SENTINEL_NOT_FOUND]


def _mount_sentinel_error(mount_caps: MountCapabilities) -> VolumeErrorKind:
    """
    Most specific reason a mount-managed volume's sentinel is missing.

    A cause the mount lifecycle recorded (e.g. NOT_AUTHORIZED →
    POLKIT_RULES_MISSING) wins, so troubleshoot points at the real fix.
    A missing fstab entry matters because udisks would then mount at
    /run/media rather than at the declared path.
    """
    specific = _mount_lifecycle_failure_error(mount_caps)
    match mount_caps:
        case _ if specific is not None:
            return specific
        case MountCapabilities(device_present=False):
            return VolumeErrorKind.DEVICE_NOT_PRESENT
        case MountCapabilities(has_fstab_entry=False):
            return VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH
        case MountCapabilities(mounted=False):
            return VolumeErrorKind.VOLUME_NOT_MOUNTED
        case _:
            return VolumeErrorKind.SENTINEL_NOT_FOUND


def _mount_lifecycle_failure_error(
    mount_caps: MountCapabilities,
) -> VolumeErrorKind | None:
    """
    Return the error a recorded mount lifecycle failure upgrades to.

    ``None`` when no upgrade is available; the caller falls back to DEVICE_NOT_PRESENT /
    VOLUME_NOT_MOUNTED / SENTINEL_NOT_FOUND.
    """
    match mount_caps.mount_failure_reason:
        case MountFailureKind.NOT_AUTHORIZED:
            return VolumeErrorKind.POLKIT_RULES_MISSING
        case MountFailureKind.PASSPHRASE_NOT_AVAILABLE:
            return VolumeErrorKind.PASSPHRASE_NOT_AVAILABLE
        case MountFailureKind.UNLOCK_FAILED:
            return VolumeErrorKind.UNLOCK_FAILED
        case MountFailureKind.MOUNT_FAILED | MountFailureKind.PROBE_FAILED:
            return VolumeErrorKind.MOUNT_FAILED
        case _:
            return None
