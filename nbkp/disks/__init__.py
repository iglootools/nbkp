"""Volume mount lifecycle management."""

from .auth import (
    POLKIT_RULES_PATH,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
)
from .detection import (
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_effective_path,
    resolve_target_device,
)
from .lifecycle import (
    MountFailureReason,
    MountResult,
    UmountResult,
    mount_count,
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)
from .observation import (
    MountObservation,
    apply_effective_paths,
    build_mount_observations,
)
from .output import (
    MountStatusData,
    MountStatusLabel,
    build_mount_status_json,
    build_mount_status_table,
    display_name,
    mount_state_icon,
)
from .udisks import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_unmount_command,
    cleartext_mapper_name,
)

__all__ = [
    "POLKIT_RULES_PATH",
    "AuthRules",
    "MountFailureReason",
    "MountObservation",
    "MountResult",
    "MountStatusData",
    "MountStatusLabel",
    "UmountResult",
    "apply_effective_paths",
    "build_lock_command",
    "build_mount_command",
    "build_mount_observations",
    "build_mount_status_json",
    "build_mount_status_table",
    "build_unlock_command",
    "build_unmount_command",
    "cleartext_mapper_name",
    "detect_device_present",
    "discover_cleartext_device",
    "display_name",
    "find_mountpoint",
    "generate_auth_rules",
    "generate_polkit_rules",
    "mount_count",
    "mount_state_icon",
    "mount_volume",
    "mount_volumes",
    "resolve_effective_path",
    "resolve_target_device",
    "umount_volume",
    "umount_volumes",
]
