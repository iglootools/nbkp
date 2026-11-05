"""Layer 2 — Volume: sentinel, mount config/state, filesystem properties."""

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ...config import (
    MountConfig,
    RemoteVolume,
    Volume,
)
from ...disks import (
    MountCapabilities,
    MountFailureReason,
)
from ...fsprotocol import (
    VOLUME_SENTINEL,
)
from .ssh import SshEndpointStatus


class VolumeError(str, enum.Enum):
    """SSH reachability and host tool availability errors are
    ``SshEndpointError``s, not volume errors.
    """

    VOLUME_NOT_MOUNTED = "volume not mounted"

    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"

    DEVICE_NOT_PRESENT = "device not plugged in"
    UNLOCK_FAILED = "failed to unlock luks encrypted device"
    MOUNT_FAILED = "failed to mount volume"
    PASSPHRASE_NOT_AVAILABLE = "passphrase not available"

    # fstab config: fixed-path (Option A) mounts only
    FSTAB_MOUNTPOINT_MISMATCH = "no fstab entry maps the device to the configured path"

    # Auth rules (polkit only — udisks has no sudoers path)
    POLKIT_RULES_MISSING = "polkit rules not configured"

    # Cascade — lower layer inactive
    SSH_ENDPOINT_INACTIVE = "ssh endpoint inactive"


INACTIVE_VOLUME_ERRORS: frozenset[VolumeError] = frozenset(
    {
        VolumeError.SENTINEL_NOT_FOUND,
        VolumeError.DEVICE_NOT_PRESENT,
        VolumeError.VOLUME_NOT_MOUNTED,
        VolumeError.SSH_ENDPOINT_INACTIVE,
    }
)


class VolumeCapabilities(BaseModel):
    """Volume-level capabilities, computed once per reachable volume.

    Host-level tool availability (rsync, btrfs, stat, findmnt) lives
    on ``HostToolCapabilities`` at the SSH endpoint level.  This model
    captures only volume-specific filesystem properties.
    """

    model_config = ConfigDict(frozen=True)

    sentinel_exists: bool
    is_btrfs_filesystem: bool | None
    """``None`` when the probe (``stat -f``) failed: type unknown."""
    hardlink_supported: bool
    btrfs_user_subvol_rm: bool
    mount: MountCapabilities | None = None


class VolumeDiagnostics(BaseModel):
    """Observed state of a volume.

    Pure diagnostics — no interpretation of what constitutes an error.
    ``VolumeStatus.from_diagnostics`` translates these into
    ``VolumeError`` values.  SSH reachability and location exclusion
    live on ``SshEndpointDiagnostics``.
    """

    model_config = ConfigDict(frozen=True)

    capabilities: VolumeCapabilities | None = None
    """``None`` when the volume sentinel is missing and no mount config
    to probe (rare — usually at least sentinel_exists is set)."""


class VolumeStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    config: Volume
    ssh_endpoint_status: SshEndpointStatus
    diagnostics: VolumeDiagnostics | None
    """``None`` when the SSH endpoint is inactive (unreachable or
    excluded)."""
    errors: list[VolumeError]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        config: Volume,
        ssh_endpoint_status: SshEndpointStatus,
        diagnostics: VolumeDiagnostics | None,
    ) -> VolumeStatus:
        """Only remote volumes cascade an inactive SSH endpoint: localhost is
        always reachable, and its tool errors surface at the sync level as
        ``SyncError.ENDPOINT_HOST_ERRORS`` instead.
        """
        return VolumeStatus(
            slug=slug,
            config=config,
            ssh_endpoint_status=ssh_endpoint_status,
            diagnostics=diagnostics,
            errors=(
                [VolumeError.SSH_ENDPOINT_INACTIVE]
                if isinstance(config, RemoteVolume) and not ssh_endpoint_status.active
                else _volume_errors(diagnostics, config.mount)
                if diagnostics is not None
                else []
            ),
        )


def _volume_errors(
    diag: VolumeDiagnostics,
    mount: MountConfig | None = None,
) -> list[VolumeError]:
    match (diag.capabilities, mount):
        case (None | VolumeCapabilities(sentinel_exists=True), _):
            return []
        case (VolumeCapabilities(mount=MountCapabilities() as caps), MountConfig()):
            return [_mount_sentinel_error(caps)]
        case _:
            return [VolumeError.SENTINEL_NOT_FOUND]


def _mount_sentinel_error(mount_caps: MountCapabilities) -> VolumeError:
    """Most specific reason a mount-managed volume's sentinel is missing.

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
            return VolumeError.DEVICE_NOT_PRESENT
        case MountCapabilities(has_fstab_entry=False):
            return VolumeError.FSTAB_MOUNTPOINT_MISMATCH
        case MountCapabilities(mounted=False):
            return VolumeError.VOLUME_NOT_MOUNTED
        case _:
            return VolumeError.SENTINEL_NOT_FOUND


def _mount_lifecycle_failure_error(
    mount_caps: MountCapabilities,
) -> VolumeError | None:
    """Returns ``None`` when no upgrade is available — caller falls back to
    DEVICE_NOT_PRESENT / VOLUME_NOT_MOUNTED / SENTINEL_NOT_FOUND.
    """
    match mount_caps.mount_failure_reason:
        case MountFailureReason.NOT_AUTHORIZED:
            return VolumeError.POLKIT_RULES_MISSING
        case MountFailureReason.PASSPHRASE_NOT_AVAILABLE:
            return VolumeError.PASSPHRASE_NOT_AVAILABLE
        case MountFailureReason.UNLOCK_FAILED:
            return VolumeError.UNLOCK_FAILED
        case MountFailureReason.MOUNT_FAILED | MountFailureReason.PROBE_FAILED:
            return VolumeError.MOUNT_FAILED
        case _:
            return None
