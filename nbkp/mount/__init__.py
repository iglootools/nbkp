"""Volume mount lifecycle management."""

from .auth import (
    POLKIT_RULES_PATH,
    SUDOERS_RULES_PATH,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
    generate_sudoers_rules,
)
from .strategy import DirectMountStrategy, MountStrategy, SystemdMountStrategy
from .detection import (
    detect_device_present,
    detect_device_unlocked,
    detect_systemd_cryptsetup_path,
    detect_volume_mounted,
    resolve_mount_strategy,
    resolve_mount_unit,
)
from .lifecycle import (
    MountResult,
    UmountResult,
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)
from .systemd import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_umount_command,
)

__all__ = [
    "AuthRules",
    "DirectMountStrategy",
    "MountStrategy",
    "MountResult",
    "POLKIT_RULES_PATH",
    "SUDOERS_RULES_PATH",
    "SystemdMountStrategy",
    "UmountResult",
    "build_lock_command",
    "build_mount_command",
    "build_unlock_command",
    "build_umount_command",
    "detect_device_present",
    "detect_device_unlocked",
    "detect_systemd_cryptsetup_path",
    "detect_volume_mounted",
    "generate_auth_rules",
    "generate_polkit_rules",
    "generate_sudoers_rules",
    "mount_volume",
    "mount_volumes",
    "resolve_mount_strategy",
    "resolve_mount_unit",
    "umount_volume",
    "umount_volumes",
]
