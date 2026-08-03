"""Volume mount lifecycle management."""

from .auth import (
    POLKIT_RULES_PATH,
    SUDOERS_RULES_PATH,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
    generate_sudoers_rules,
)
from .detection import (
    StrategyErrorReason,
    StrategyResolutionError,
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    resolve_mount_strategy,
    resolve_mount_unit,
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
from .observation import MountObservation, build_mount_observations
from .output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
    display_name,
    mount_state_icon,
)
from .strategy import DirectMountStrategy, MountStrategy, SystemdMountStrategy
from .systemd import (
    build_attach_luks_command,
    build_close_luks_command,
    build_mount_command,
    build_umount_command,
)

__all__ = [
    "POLKIT_RULES_PATH",
    "SUDOERS_RULES_PATH",
    "AuthRules",
    "DirectMountStrategy",
    "MountFailureReason",
    "MountObservation",
    "MountResult",
    "MountStatusData",
    "MountStrategy",
    "StrategyErrorReason",
    "StrategyResolutionError",
    "SystemdMountStrategy",
    "UmountResult",
    "build_attach_luks_command",
    "build_close_luks_command",
    "build_mount_command",
    "build_mount_observations",
    "build_mount_status_json",
    "build_mount_status_table",
    "build_umount_command",
    "detect_device_present",
    "detect_luks_attached",
    "detect_systemd_cryptsetup_path",
    "display_name",
    "generate_auth_rules",
    "generate_polkit_rules",
    "generate_sudoers_rules",
    "mount_count",
    "mount_state_icon",
    "mount_volume",
    "mount_volumes",
    "resolve_mount_strategy",
    "resolve_mount_unit",
    "umount_volume",
    "umount_volumes",
]
