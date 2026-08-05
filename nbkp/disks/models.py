"""Mount-related data models: tool capabilities and mount state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
    """The mountpoint the fstab entry maps the device to, if any."""

    # Runtime mount state (probed during observation)
    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    cleartext_device: str | None = None
    effective_path: str | None = None
    mount_failure_reason: str | None = None
    """Raw ``MountFailureReason`` value (string) when the lifecycle step
    failed for a known cause. Used by preflight to upgrade the generic
    VOLUME_NOT_MOUNTED to a more specific error like POLKIT_RULES_MISSING.
    Stored as a string here to keep ``models.py`` free of cross-module
    enum imports."""
