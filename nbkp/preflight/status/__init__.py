"""Runtime status types for the 4-layer preflight error model.

Capabilities are probed at the level where they physically exist,
but interpreted as errors at the level where the requirement originates.
For example: ``has_btrfs`` is probed at the SSH endpoint level (it's a host
tool), ``is_btrfs_filesystem`` is probed at the volume level (it's a
filesystem property), but both become errors at the sync endpoint level
(because the endpoint config determines whether btrfs is needed).

4-layer error hierarchy:

1. **SSH Endpoint** — Reachability, host tool availability
2. **Volume** — Sentinel, mount config/state, filesystem properties
3. **Sync Endpoint** — Endpoint sentinel, dirs, symlinks, writability,
   capability-gated errors
4. **Sync** — Disabled, ``latest → /dev/null`` interpretation

Each lower layer gates the next: SSH endpoint must be active for volume
checks to run, volume must be active for endpoint checks, etc.

One module per layer — :mod:`.ssh`, :mod:`.volume`, :mod:`.endpoint`,
:mod:`.sync` — each holding the layer's error enum, its ``INACTIVE_*`` set,
diagnostics and status models, and the interpretation of diagnostics into
errors; :mod:`.result` holds :class:`PreflightResult`.  Each layer depends
only on the ones below it (ssh ← volume ← endpoint ← sync ← result).
"""

# The mount models are part of the diagnostics (``VolumeCapabilities``,
# ``SshEndpointDiagnostics``) and were always reachable from here; they are
# defined in ``nbkp.disks``.
from ...disks import MountCapabilities, MountToolCapabilities
from .endpoint import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SourceEndpointError,
    SourceEndpointStatus,
)
from .result import (
    PreflightResult,
)
from .ssh import (
    INACTIVE_SSH_ERRORS,
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointError,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SshEndpointWarning,
)
from .sync import (
    INACTIVE_SYNC_ERRORS,
    SyncError,
    SyncStatus,
)
from .volume import (
    INACTIVE_VOLUME_ERRORS,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)

__all__ = [
    "INACTIVE_DST_ENDPOINT_ERRORS",
    "INACTIVE_SRC_ENDPOINT_ERRORS",
    "INACTIVE_SSH_ERRORS",
    "INACTIVE_SYNC_ERRORS",
    "INACTIVE_VOLUME_ERRORS",
    "BtrfsStagingSubvolumeDiagnostics",
    "DestinationEndpointDiagnostics",
    "DestinationEndpointError",
    "DestinationEndpointStatus",
    "HostToolCapabilities",
    "LatestSymlinkState",
    "MountCapabilities",
    "MountToolCapabilities",
    "PreflightResult",
    "SnapshotDirsDiagnostics",
    "SourceEndpointDiagnostics",
    "SourceEndpointError",
    "SourceEndpointStatus",
    "SshEndpointDiagnostics",
    "SshEndpointError",
    "SshEndpointStatus",
    "SshEndpointToolNeeds",
    "SshEndpointWarning",
    "SyncError",
    "SyncStatus",
    "VolumeCapabilities",
    "VolumeDiagnostics",
    "VolumeError",
    "VolumeStatus",
]
