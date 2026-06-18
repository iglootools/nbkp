"""Drive detection and state queries for mount management.

This is morally equivalent to the `preflight.queries` module, but the mounting logic runs before any preflight checks in the lifecycle,
so these functions are separate and have no dependencies on preflight code.
"""

from __future__ import annotations

from ..config import (
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..remote.dispatch import run_on_volume


def detect_device_present(
    volume: Volume,
    device_uuid: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a device UUID exists in ``/dev/disk/by-uuid/``.

    Uses ``test -e`` (not ``-f``) because these entries are symlinks.
    Maintained by udev — no root, no parsing, works on all
    systemd-based Linux.
    """
    result = run_on_volume(
        ["test", "-e", f"/dev/disk/by-uuid/{device_uuid}"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0


def discover_cleartext_device(
    volume: Volume,
    luks_uuid: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Discover the unlocked cleartext device for a LUKS container.

    Runs ``lsblk -rno NAME,TYPE /dev/disk/by-uuid/<luks-uuid>`` and returns
    ``/dev/mapper/<name>`` for the ``crypt`` child, or ``None`` when the
    container is still locked (no crypt child) or the device is absent.

    Discovering the device (rather than assuming ``luks-<uuid>``) makes nbkp
    agnostic to whether a ``/etc/crypttab`` entry renamed the mapper.
    """
    result = run_on_volume(
        ["lsblk", "-rno", "NAME,TYPE", f"/dev/disk/by-uuid/{luks_uuid}"],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "crypt":
            return f"/dev/mapper/{parts[0]}"
    return None


def resolve_target_device(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Resolve the block device to mount/unmount for a volume.

    For unencrypted volumes this is ``/dev/disk/by-uuid/<fs-uuid>``.  For
    encrypted volumes it is the discovered cleartext mapper, or ``None`` when
    the container is still locked.
    """
    if mount_config.encryption is None:
        return f"/dev/disk/by-uuid/{mount_config.device_uuid}"
    return discover_cleartext_device(
        volume, mount_config.device_uuid, resolved_endpoints
    )


def find_mountpoint(
    volume: Volume,
    device: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Return the current mountpoint of ``device``, or ``None`` if unmounted.

    Runs ``findmnt --source <device> -n -o TARGET``.  Authoritative for both
    fstab-declared paths and udisks's ``/run/media`` defaults, and works for
    the already-mounted idempotent case.
    """
    # udisks2 could answer this via the Filesystem.MountPoints property
    # (``udisksctl info -b <device>``), but we use findmnt for every mount query:
    # it reads the kernel mount table directly (one source of truth, not udisks's
    # cached view), yields clean ``-o``-selected columns, and behaves the same
    # locally and over SSH. The query that forces the choice — the live mount
    # option string — has no udisks equivalent; see
    # preflight.snapshot_checks.check_btrfs_mount_option.
    result = run_on_volume(
        ["findmnt", "--source", device, "-n", "-o", "TARGET"],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        return None
    target = result.stdout.strip().splitlines()
    return target[0].strip() if target and target[0].strip() else None


def resolve_effective_path(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Resolve where a mount-managed volume is (or should be) located.

    When ``volume.path`` is declared it is authoritative (Option A — fstab
    mounts the device there).  When omitted (Option B), the mountpoint udisks
    chose is discovered via ``findmnt``; returns ``None`` if the device is not
    currently mounted (or the LUKS container is still locked).
    """
    if volume.path is not None:
        return volume.path
    device = resolve_target_device(volume, mount_config, resolved_endpoints)
    if device is None:
        return None
    return find_mountpoint(volume, device, resolved_endpoints)
