"""Preflight checks: observe volumes, endpoints and syncs, then interpret them.

This package's public interface: other modules (``run``, ``sync``,
``cliwiring``, ``snapshots``) import from ``nbkp.preflight`` rather than from
its submodules (``status``, ``severity``, ``strictness``, ``probes``, …),
which are implementation details. Presentation is not re-exported here, so
importing ``nbkp.preflight`` never loads Rich: display code imports
``nbkp.preflight.output`` explicitly.
"""

from .checks import (
    check_all_syncs,
    check_step_count,
)
from .endpoint_checks import (
    observe_destination_endpoint,
    observe_source_endpoint,
)
from .messages import format_problem, format_problems
from .severity import PreflightErrorKind, severity_for_error, severity_for_errors
from .status import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    INACTIVE_SSH_ERRORS,
    INACTIVE_SYNC_ERRORS,
    INACTIVE_VOLUME_ERRORS,
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    HostToolCapabilities,
    LatestSymlinkState,
    MountCapabilities,
    MountToolCapabilities,
    PreflightResult,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SyncErrorKind,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from .strictness import has_fatal_errors, is_fatal
from .volume_checks import (
    check_volume_capabilities,
    observe_ssh_endpoint,
    observe_volume,
)

__all__ = [
    "INACTIVE_DST_ENDPOINT_ERRORS",
    "INACTIVE_SRC_ENDPOINT_ERRORS",
    "INACTIVE_SSH_ERRORS",
    "INACTIVE_SYNC_ERRORS",
    "INACTIVE_VOLUME_ERRORS",
    "BtrfsStagingSubvolumeDiagnostics",
    "DestinationEndpointDiagnostics",
    "DestinationEndpointErrorKind",
    "DestinationEndpointStatus",
    "HostToolCapabilities",
    "LatestSymlinkState",
    "MountCapabilities",
    "MountToolCapabilities",
    "PreflightErrorKind",
    "PreflightResult",
    "SnapshotDirsDiagnostics",
    "SourceEndpointDiagnostics",
    "SourceEndpointErrorKind",
    "SourceEndpointStatus",
    "SshEndpointDiagnostics",
    "SshEndpointErrorKind",
    "SshEndpointStatus",
    "SshEndpointToolNeeds",
    "SyncErrorKind",
    "SyncStatus",
    "VolumeCapabilities",
    "VolumeDiagnostics",
    "VolumeErrorKind",
    "VolumeStatus",
    "check_all_syncs",
    "check_step_count",
    "check_volume_capabilities",
    "format_problem",
    "format_problems",
    "has_fatal_errors",
    "is_fatal",
    "observe_destination_endpoint",
    "observe_source_endpoint",
    "observe_ssh_endpoint",
    "observe_volume",
    "severity_for_error",
    "severity_for_errors",
]
