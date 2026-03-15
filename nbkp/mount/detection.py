"""Drive detection and state queries for mount management.

This is morally equivalent to the `preflight.queries` module, but the mounting logic runs before any preflight checks in the lifecycle,
so these functions are separate and have no dependencies on preflight code.
"""

from __future__ import annotations

import shutil

from ..config import (
    Config,
    LocalVolume,
    MountConfig,
    ResolvedEndpoints,
    Volume,
)
from ..remote.dispatch import run_on_volume
from .auth import CRYPTSETUP_PATHS
from .strategy import DirectMountStrategy, MountStrategy, SystemdMountStrategy


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


def detect_luks_attached(
    volume: Volume,
    mapper_name: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Check if a LUKS device is unlocked (mapper exists)."""
    result = run_on_volume(
        ["test", "-b", f"/dev/mapper/{mapper_name}"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0


def detect_systemd_cryptsetup_path(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Find the systemd-cryptsetup binary path on the target host.

    Checks common locations: ``/usr/lib/systemd/systemd-cryptsetup``
    and ``/lib/systemd/systemd-cryptsetup``.
    """
    return next(
        (
            path
            for path in CRYPTSETUP_PATHS
            if run_on_volume(
                ["test", "-x", path], volume, resolved_endpoints
            ).returncode
            == 0
        ),
        None,
    )


def resolve_mount_unit(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Derive the systemd mount unit name for a volume path.

    Runs ``systemd-escape --path <volume-path>`` on the target host
    and appends ``.mount``. Returns ``None`` if ``systemd-escape``
    is not available.
    """
    result = run_on_volume(
        ["systemd-escape", "--path", volume.path],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        return None
    escaped = result.stdout.strip()
    return f"{escaped}.mount" if escaped else None


def _has_systemctl(volume: Volume, resolved_endpoints: ResolvedEndpoints) -> bool:
    """Check if systemctl is available on the volume's host."""
    match volume:
        case LocalVolume():
            return shutil.which("systemctl") is not None
        case _:
            return (
                run_on_volume(
                    ["which", "systemctl"], volume, resolved_endpoints
                ).returncode
                == 0
            )


def _resolve_mount_strategy(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> MountStrategy:
    """Resolve a single volume's mount strategy."""
    strategy = mount_config.strategy

    use_systemd = strategy == "systemd" or (
        strategy == "auto" and _has_systemctl(volume, resolved_endpoints)
    )

    if use_systemd:
        mount_unit = resolve_mount_unit(volume, resolved_endpoints)
        cryptsetup_path = (
            detect_systemd_cryptsetup_path(volume, resolved_endpoints)
            if mount_config.encryption is not None
            else None
        )
        return SystemdMountStrategy(
            mount_unit=mount_unit or "",
            cryptsetup_path=cryptsetup_path,
        )
    else:
        return DirectMountStrategy(volume_path=volume.path)


def resolve_mount_strategy(
    cfg: Config,
    resolved_endpoints: ResolvedEndpoints,
    names: list[str] | None,
) -> dict[str, MountStrategy]:
    """Resolve a ``MountStrategy`` per volume with mount config.

    ``auto`` probes for systemctl: present → ``SystemdMountStrategy``,
    absent → ``DirectMountStrategy``.
    """
    return {
        slug: _resolve_mount_strategy(vol, vol.mount, resolved_endpoints)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (names is None or slug in names)
    }
