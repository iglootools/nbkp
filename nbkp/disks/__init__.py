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
    mount_volume,
    mount_volume_count,
    mount_volumes,
    umount_volume,
    umount_volumes,
)
from .observation import MountObservation, build_mount_observations
from .output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
    mount_state_icon,
    volume_display_name,
)
from .systemd import (
    build_close_luks_command,
    build_mount_command,
    build_attach_luks_command,
    build_umount_command,
)

__all__ = [
    "AuthRules",
    "DirectMountStrategy",
    "MountFailureReason",
    "MountObservation",
    "MountStatusData",
    "MountStrategy",
    "MountResult",
    "POLKIT_RULES_PATH",
    "SUDOERS_RULES_PATH",
    "StrategyErrorReason",
    "StrategyResolutionError",
    "SystemdMountStrategy",
    "UmountResult",
    "build_mount_observations",
    "build_mount_status_json",
    "build_mount_status_table",
    "build_close_luks_command",
    "build_mount_command",
    "build_attach_luks_command",
    "build_umount_command",
    "detect_device_present",
    "detect_luks_attached",
    "detect_systemd_cryptsetup_path",
    "generate_auth_rules",
    "generate_polkit_rules",
    "generate_sudoers_rules",
    "mount_state_icon",
    "mount_volume",
    "mount_volume_count",
    "mount_volumes",
    "resolve_mount_strategy",
    "resolve_mount_unit",
    "umount_volume",
    "umount_volumes",
    "volume_display_name",
]
