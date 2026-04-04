"""Systemd command builders for mount/umount and LUKS attach/close.

All functions are pure — they receive pre-resolved values (mount unit names,
cryptsetup paths) and return command lists. No host calls at this layer.
"""

from __future__ import annotations


def build_attach_luks_command(
    cryptsetup_path: str,
    mapper_name: str,
    device_uuid: str,
) -> list[str]:
    """Build command to attach a LUKS volume via systemd-cryptsetup.

    Passphrase is read from stdin (``/dev/stdin``).

    Returns e.g.::

        ["sudo", "/usr/lib/systemd/systemd-cryptsetup",
         "attach", "seagate8tb",
         "/dev/disk/by-uuid/5941f273-...", "/dev/stdin", "luks"]
    """
    return [
        "sudo",
        cryptsetup_path,
        "attach",
        mapper_name,
        f"/dev/disk/by-uuid/{device_uuid}",
        "/dev/stdin",
        "luks",
    ]


def build_mount_command(mount_unit: str) -> list[str]:
    """Build command to mount a volume via systemctl.

    Returns e.g.::

        ["systemctl", "start", "mnt-seagate8tb.mount"]
    """
    return ["systemctl", "start", mount_unit]


def build_umount_command(mount_unit: str) -> list[str]:
    """Build command to umount a volume via systemctl.

    Returns e.g.::

        ["systemctl", "stop", "mnt-seagate8tb.mount"]
    """
    return ["systemctl", "stop", mount_unit]


def build_detect_mounted_command(mount_unit: str) -> list[str]:
    """Build command to check if a volume is mounted via systemctl.

    Returns e.g.::

        ["systemctl", "is-active", "mnt-seagate8tb.mount", "--quiet"]
    """
    return ["systemctl", "is-active", mount_unit, "--quiet"]


def build_close_luks_command(mapper_name: str) -> list[str]:
    """Build command to close a LUKS volume via systemctl.

    Returns e.g.::

        ["systemctl", "stop", "systemd-cryptsetup@seagate8tb.service"]
    """
    return [
        "systemctl",
        "stop",
        f"systemd-cryptsetup@{mapper_name}.service",
    ]
