"""
Preflight checks: observe volumes, endpoints and syncs, then interpret them.

This package's public interface: other modules (``run``, ``sync``,
``cliwiring``, ``snapshots``) import from ``nbkp.preflight`` rather than from
its submodules (``status``, ``severity``, ``strictness``, ``probes``, …),
which are implementation details. Presentation is not re-exported here, so
importing ``nbkp.preflight`` never loads Rich: display code imports
``nbkp.preflight.output`` explicitly.
"""

from nbkp.preflight.checks import check_all_syncs, check_step_count
from nbkp.preflight.endpoint_checks import (
    observe_destination_endpoint,
    observe_source_endpoint,
)
from nbkp.preflight.probes import VolumeReadError, list_snapshot_dir, read_symlink
from nbkp.preflight.severity import (
    PreflightErrorKind,
    severity_for_error,
    severity_for_errors,
)
from nbkp.preflight.status import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    INACTIVE_SSH_ERRORS,
    INACTIVE_SYNC_ERRORS,
    INACTIVE_VOLUME_ERRORS,
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    DestinationEndpointWarningKind,
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
from nbkp.preflight.strictness import has_fatal_errors, is_fatal
from nbkp.preflight.troubleshoot import (
    Layer,
    RemediationFacts,
    TroubleshootIssue,
    collect_issues,
    gather_remediation_facts,
)
from nbkp.preflight.volume_checks import (
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
    "DestinationEndpointWarningKind",
    "HostToolCapabilities",
    "LatestSymlinkState",
    "Layer",
    "MountCapabilities",
    "MountToolCapabilities",
    "PreflightErrorKind",
    "PreflightResult",
    "RemediationFacts",
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
    "TroubleshootIssue",
    "VolumeCapabilities",
    "VolumeDiagnostics",
    "VolumeErrorKind",
    "VolumeReadError",
    "VolumeStatus",
    "check_all_syncs",
    "check_step_count",
    "check_volume_capabilities",
    "collect_issues",
    "gather_remediation_facts",
    "has_fatal_errors",
    "is_fatal",
    "list_snapshot_dir",
    "observe_destination_endpoint",
    "observe_source_endpoint",
    "observe_ssh_endpoint",
    "observe_volume",
    "read_symlink",
    "severity_for_error",
    "severity_for_errors",
]
