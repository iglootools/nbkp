"""Volume mount lifecycle management.

This package's public interface: other modules (``preflight``, ``commands``,
``run``, ``demo``) import from ``nbkp.disks`` rather than from its
submodules (``udisks``, ``detection``, ``mount_checks``, …), which are
implementation details.
"""

from .auth import (
    POLKIT_RULES_PATH,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
)
from .context import managed_mount
from .detection import (
    DeviceProbeError,
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
from .models import MountCapabilities, MountToolCapabilities
from .mount_checks import (
    check_mount_capabilities,
    check_mount_status,
    probe_mount_tools,
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
    device_fail_severity,
    display_name,
    luks_fail_severity,
    mount_state_icon,
    mounted_fail_severity,
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
    "DeviceProbeError",
    "MountCapabilities",
    "MountFailureReason",
    "MountObservation",
    "MountResult",
    "MountStatusData",
    "MountStatusLabel",
    "MountToolCapabilities",
    "UmountResult",
    "apply_effective_paths",
    "build_lock_command",
    "build_mount_command",
    "build_mount_observations",
    "build_mount_status_json",
    "build_mount_status_table",
    "build_unlock_command",
    "build_unmount_command",
    "check_mount_capabilities",
    "check_mount_status",
    "cleartext_mapper_name",
    "detect_device_present",
    "device_fail_severity",
    "discover_cleartext_device",
    "display_name",
    "find_mountpoint",
    "generate_auth_rules",
    "generate_polkit_rules",
    "luks_fail_severity",
    "managed_mount",
    "mount_count",
    "mount_state_icon",
    "mount_volume",
    "mount_volumes",
    "mounted_fail_severity",
    "probe_mount_tools",
    "resolve_effective_path",
    "resolve_target_device",
    "umount_volume",
    "umount_volumes",
]
