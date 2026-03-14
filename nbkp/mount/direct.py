"""Direct command builders for mount/umount and LUKS lock/unlock.

Used on hosts without systemd (e.g. Docker containers). All functions
are pure — they receive pre-resolved values and return command lists.
"""

from __future__ import annotations


def build_unlock_command(
    mapper_name: str,
    device_uuid: str,
) -> list[str]:
    """Build command to unlock a LUKS volume via cryptsetup.

    Passphrase is read from stdin (``-`` key-file argument).

    Returns e.g.::

        ["sudo", "cryptsetup", "open", "--type", "luks",
         "/dev/disk/by-uuid/5941f273-...", "seagate8tb", "-"]
    """
    return [
        "sudo",
        "cryptsetup",
        "open",
        "--type",
        "luks",
        f"/dev/disk/by-uuid/{device_uuid}",
        mapper_name,
        "-",
    ]


def build_lock_command(mapper_name: str) -> list[str]:
    """Build command to lock a LUKS volume via cryptsetup close.

    Returns e.g.::

        ["sudo", "cryptsetup", "close", "seagate8tb"]
    """
    return ["sudo", "cryptsetup", "close", mapper_name]


def build_mount_command(volume_path: str) -> list[str]:
    """Build command to mount a volume via sudo mount.

    The device and mount options come from fstab (mirroring how the
    systemd strategy reads options from the unit file).

    Returns e.g.::

        ["sudo", "mount", "/mnt/seagate8tb"]
    """
    return ["sudo", "mount", volume_path]


def build_umount_command(volume_path: str) -> list[str]:
    """Build command to umount a volume via sudo umount.

    Returns e.g.::

        ["sudo", "umount", "/mnt/seagate8tb"]
    """
    return ["sudo", "umount", volume_path]
