"""Layer 2 — Volume: sentinel, mount config/state, filesystem properties."""

from __future__ import annotations

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
    """Volume-level errors.

    Only volume-specific errors remain here.  SSH reachability and host
    tool availability errors moved to ``SshEndpointError``.
    """

    # Mount management — runtime state
    VOLUME_NOT_MOUNTED = "volume not mounted"

    # Sentinel management
    SENTINEL_NOT_FOUND = f"{VOLUME_SENTINEL} volume sentinel not found"

    # Mount management — lifecycle errors
    DEVICE_NOT_PRESENT = "device not plugged in"
    UNLOCK_FAILED = "failed to unlock luks encrypted device"
    MOUNT_FAILED = "failed to mount volume"
    PASSPHRASE_NOT_AVAILABLE = "passphrase not available"

    # Mount management — fstab config (fixed-path / Option A only)
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
    """Volume-level capabilities.
    ``None`` when the volume sentinel is missing and no mount config
    to probe (rare — usually at least sentinel_exists is set)."""


class VolumeStatus(BaseModel):
    """Runtime status of a volume."""

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
        """Create status by interpreting diagnostics into errors.

        Only remote volumes cascade an inactive SSH endpoint: localhost is
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


# ── Layer 2 error interpretation ───────────────────────────


def _volume_errors(
    diag: VolumeDiagnostics,
    mount: MountConfig | None = None,
) -> list[VolumeError]:
    """Translate volume diagnostics into VolumeError values."""
    match (diag.capabilities, mount):
        case (None | VolumeCapabilities(sentinel_exists=True), _):
            return []
        case (VolumeCapabilities(mount=MountCapabilities() as caps), MountConfig()):
            return [_mount_sentinel_error(caps)]
        case _:
            return [VolumeError.SENTINEL_NOT_FOUND]


def _mount_sentinel_error(mount_caps: MountCapabilities) -> VolumeError:
    """Most specific reason a mount-managed volume's sentinel is missing.

    Order:

    1. Lifecycle recorded a known cause (e.g. NOT_AUTHORIZED →
       POLKIT_RULES_MISSING) — surface that so troubleshoot points at the
       real fix.
    2. device not plugged in — DEVICE_NOT_PRESENT.
    3. path declared but no fstab entry maps the device there —
       FSTAB_MOUNTPOINT_MISMATCH (udisks would mount at /run/media).
    4. device present but unmounted — VOLUME_NOT_MOUNTED.
    5. Otherwise, fall back to SENTINEL_NOT_FOUND.
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
    """Map a lifecycle ``MountFailureReason`` to a more specific VolumeError.

    Returns ``None`` when no upgrade is available — caller falls back to
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
