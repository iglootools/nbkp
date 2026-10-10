"""Mount-related data models: tool capabilities, mount state, failure reasons."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ..config.protocol.volume import LocalVolume, RemoteVolume


class MountFailureReason(str, enum.Enum):
    """Structured reason for a mount lifecycle failure.

    Lives here (not in ``lifecycle``) so that ``MountCapabilities`` can carry
    it as a typed field and preflight can ``match`` on members instead of
    string literals.
    """

    DEVICE_NOT_PRESENT = "device_not_present"
    PASSPHRASE_NOT_AVAILABLE = "passphrase_not_available"
    UNLOCK_FAILED = "unlock_failed"
    MOUNT_FAILED = "mount_failed"
    NOT_AUTHORIZED = "not_authorized"
    UDISKS_NOT_AVAILABLE = "udisks_not_available"
    PROBE_FAILED = "probe_failed"
    """A read-only probe (e.g. ``lsblk``) failed, so the device state is
    unknown — distinct from "locked" or "not mounted"."""
    UNREACHABLE = "unreachable"


class MountToolCapabilities(BaseModel):
    """Mount management tool availability on a host.

    Probed once per SSH endpoint when any volume on that host has
    mount config.  Tool *availability* is host-level; tool *need*
    depends on volume config and is evaluated during error interpretation.
    """

    model_config = ConfigDict(frozen=True)

    # udisks
    has_udisksctl: bool | None = None
    udisksd_running: bool | None = None
    has_btrfs_module: bool | None = None

    # Detection helpers
    has_findmnt: bool | None = None
    has_lsblk: bool | None = None


class MountCapabilities(BaseModel):
    """Volume-specific mount diagnostics (config checks + runtime state).

    Host-level tool availability (udisksctl, udisksd, etc.) lives on
    ``MountToolCapabilities`` at the SSH endpoint level.  This model captures
    only volume-specific config validation and runtime mount state.
    """

    model_config = ConfigDict(frozen=True)

    # fstab config check (only meaningful when ``volume.path`` is declared —
    # udisks needs an fstab entry mapping the device to that fixed path)
    has_fstab_entry: bool | None = None
    fstab_target: str | None = None
    """The mountpoint of the fstab entry for ``volume.path``, if any."""
    fstab_source: str | None = None
    """The device spec of that fstab entry (``UUID=…``, ``/dev/mapper/…``).
    ``has_fstab_entry`` is ``False`` when it names a different device."""

    # Runtime mount state (probed during observation)
    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    cleartext_device: str | None = None
    effective_path: str | None = None
    mount_failure_reason: MountFailureReason | None = None
    """Why the lifecycle step failed, when it failed for a known cause.
    Used by preflight to upgrade the generic VOLUME_NOT_MOUNTED to a more
    specific error like POLKIT_RULES_MISSING."""


def display_name(vol: LocalVolume | RemoteVolume) -> str:
    """Display name for a volume: ``ssh-endpoint:slug`` for remote, ``slug`` for local."""
    from ..config.protocol.volume import RemoteVolume

    return (
        f"{vol.ssh_endpoint}:{vol.slug}" if isinstance(vol, RemoteVolume) else vol.slug
    )
