"""
Volume mount lifecycle management.

This package's public interface: other modules (``preflight``, ``cliwiring``,
``run``, ``demo``) import from ``nbkp.disks`` rather than from its
submodules (``udisks``, ``detection``, ``mount_checks``, …), which are
implementation details. Presentation (Rich tables, icons) is not re-exported
here, so importing ``nbkp.disks`` never loads Rich: display code imports
``nbkp.disks.output`` explicitly.
"""

from nbkp.disks.auth import (
    POLKIT_RULES_PATH,
    AuthRuleBlock,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
)
from nbkp.disks.context import managed_mount, mount_phase
from nbkp.disks.detection import (
    DeviceProbeError,
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_effective_path,
    resolve_target_device,
)
from nbkp.disks.lifecycle import (
    MountFailureKind,
    MountResult,
    UmountResult,
    any_failed,
    mount_count,
    mount_volume,
    mount_volumes,
    refused_by_polkit,
    umount_volume,
    umount_volumes,
    unknown_volume_names,
)
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountCapabilities,
    MountToolCapabilities,
    UmountFailureKind,
    display_name,
)
from nbkp.disks.mount_checks import (
    check_mount_capabilities,
    check_mount_status,
    probe_mount_tools,
)
from nbkp.disks.observation import (
    MountObservation,
    apply_effective_paths,
    build_mount_observations,
)
from nbkp.disks.plan import VolumePlan, plan_incomplete, plan_json
from nbkp.disks.severity import (
    device_fail_severity,
    luks_fail_severity,
    mount_result_severity,
    mounted_fail_severity,
)
from nbkp.disks.status import (
    ProbeEndFn,
    VolumeStatus,
    managed_volumes,
    probe_volume_statuses,
)
from nbkp.disks.udisks import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_unmount_command,
    cleartext_mapper_name,
)

__all__ = [
    "POLKIT_RULES_PATH",
    "AuthRuleBlock",
    "AuthRules",
    "DeviceProbeError",
    "LifecycleFailure",
    "LifecycleStep",
    "MountCapabilities",
    "MountFailureKind",
    "MountObservation",
    "MountResult",
    "MountToolCapabilities",
    "ProbeEndFn",
    "UmountFailureKind",
    "UmountResult",
    "VolumePlan",
    "VolumeStatus",
    "any_failed",
    "apply_effective_paths",
    "build_lock_command",
    "build_mount_command",
    "build_mount_observations",
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
    "managed_volumes",
    "mount_count",
    "mount_phase",
    "mount_result_severity",
    "mount_volume",
    "mount_volumes",
    "mounted_fail_severity",
    "plan_incomplete",
    "plan_json",
    "probe_mount_tools",
    "probe_volume_statuses",
    "refused_by_polkit",
    "resolve_effective_path",
    "resolve_target_device",
    "umount_volume",
    "umount_volumes",
    "unknown_volume_names",
]
