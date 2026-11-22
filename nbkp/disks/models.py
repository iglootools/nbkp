"""The mount lifecycle's failure kinds, steps and observed device capabilities."""

import enum
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

lazy from nbkp.config.protocol.volume import LocalVolume, RemoteVolume


class MountFailureKind(enum.StrEnum):
    """
    How a mount lifecycle step failed.

    Lives here (not in ``lifecycle``) so that ``MountCapabilities`` can carry it as a
    typed field and preflight can ``match`` on members instead of string literals.
    """

    DEVICE_NOT_PRESENT = "device-not-present"
    PASSPHRASE_NOT_AVAILABLE = "passphrase-not-available"  # noqa: S105 -- a failure kind, not a secret
    UNLOCK_FAILED = "unlock-failed"
    MOUNT_FAILED = "mount-failed"
    NOT_AUTHORIZED = "not-authorized"
    UDISKS_NOT_AVAILABLE = "udisks-not-available"
    PROBE_FAILED = "probe-failed"
    """A read-only probe (e.g. ``lsblk``) failed, so the device state is
    unknown — distinct from "locked" or "not mounted"."""
    UNREACHABLE = "unreachable"


class UmountFailureKind(enum.StrEnum):
    """How an umount lifecycle step failed."""

    UNMOUNT_FAILED = "unmount-failed"
    LOCK_FAILED = "lock-failed"
    """Unmounted (or not mounted) but the LUKS container is still unlocked."""
    UDISKS_NOT_AVAILABLE = "udisks-not-available"
    PROBE_FAILED = "probe-failed"
    UNREACHABLE = "unreachable"


class LifecycleStep(enum.StrEnum):
    """A ``udisksctl`` step of the mount lifecycle."""

    UNLOCK = "unlock"
    MOUNT = "mount"
    UNMOUNT = "umount"
    LOCK = "lock"


@dataclass(frozen=True)
class LifecycleFailure:
    """
    The facts behind a failed mount or umount; the reason is on the result.

    Each field is set only when it applies, and holds data, not prose: the
    message users see is rendered by ``nbkp.disks.output``.
    """

    step: LifecycleStep | None = None
    """The udisks step that failed."""
    exit_code: int | None = None
    """Exit status of the failed command."""
    message: str | None = None
    """The failed command's stderr, or the error's first line."""
    command: str | None = None
    """A probe that failed (``lsblk``, ``findmnt``), or a tool not installed."""
    device_uuid: str | None = None
    passphrase_id: str | None = None


class MountToolCapabilities(BaseModel):
    """
    Mount management tool availability on a host.

    Probed once per SSH endpoint when any volume on that host has
    mount config.  Tool *availability* is host-level; tool *need*
    depends on volume config and is evaluated during error interpretation.
    """

    model_config = ConfigDict(frozen=True)

    has_udisksctl: bool | None = None
    udisksd_running: bool | None = None
    has_btrfs_module: bool | None = None
    has_findmnt: bool | None = None
    has_lsblk: bool | None = None


class MountCapabilities(BaseModel):
    """
    Volume-specific mount diagnostics (config checks + runtime state).

    Host-level tool availability (udisksctl, udisksd, etc.) lives on
    ``MountToolCapabilities`` at the SSH endpoint level.  This model captures
    only volume-specific config validation and runtime mount state.
    """

    model_config = ConfigDict(frozen=True)

    # fstab config check (only meaningful when ``volume.path`` is declared —
    # udisks needs an fstab entry mapping the device to that fixed path)
    has_fstab_entry: bool | None = None
    fstab_target: str | None = None  # noqa: DC06 -- in `nbkp check --json`
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
    mount_failure_reason: MountFailureKind | None = None
    """Why the lifecycle step failed, when it failed for a known cause.
    Used by preflight to upgrade the generic VOLUME_NOT_MOUNTED to a more
    specific error like POLKIT_RULES_MISSING."""


def display_name(vol: LocalVolume | RemoteVolume) -> str:
    """Return a volume's display name: ``ssh-endpoint:slug``, or ``slug`` if local."""
    return (
        f"{vol.ssh_endpoint}:{vol.slug}" if isinstance(vol, RemoteVolume) else vol.slug
    )
