"""
Fake ``preflight troubleshoot`` data covering every preflight error.

Each scenario contributes its config entries and the statuses that exercise
them, so a new preflight error is one new :class:`Scenario` added to
``SCENARIOS``.  Errors are distributed across the 4-layer hierarchy:

- SSH endpoint level: tool availability, reachability, location exclusion
- Volume level: sentinel, mount config/state
- Sync endpoint level: endpoint sentinel, dirs, symlinks, writability,
  capability-gated errors
- Sync level: disabled, latest → /dev/null interpretation
"""

from nbkp.config import Config, SyncEndpoint
from nbkp.config.testkit import base_ssh_endpoints, base_volumes
from nbkp.preflight.status.result import PreflightResult
from nbkp.preflight.testkit.common import assemble_preflight_result, demo_ssh_statuses
from nbkp.preflight.testkit.troubleshoot.host_tools import (
    BTRFS_NOT_DETECTED,
    HOST_TOOL_ERRORS,
    RSYNC_MISSING,
    RSYNC_TOO_OLD,
    TOOLS_MISSING,
)
from nbkp.preflight.testkit.troubleshoot.mounts import (
    MOUNT_BTRFS_SUBVOLRM,
    MOUNT_DEVICE_MISSING,
    MOUNT_ENCRYPTED,
    MOUNT_FSTAB_MISMATCH,
    MOUNT_LUKS_FAILED,
    MOUNT_MOUNT_FAILED,
    MOUNT_NO_PASSPHRASE,
    MOUNT_POLKIT_REFUSED,
    MOUNT_UNENCRYPTED,
)
from nbkp.preflight.testkit.troubleshoot.permissions import (
    BTRFS_PERMISSIONS,
    HARDLINK_PERMISSIONS,
    NO_SNAPSHOT_PERMISSIONS,
)
from nbkp.preflight.testkit.troubleshoot.reachability import (
    DISABLED_SYNC,
    LOCATION_EXCLUDED,
    MISSING_SENTINELS,
    STANDALONE_ENDPOINTS,
    UNAVAILABLE_VOLUMES,
    UNREADABLE_SENTINELS,
)
from nbkp.preflight.testkit.troubleshoot.scenario import (
    LAPTOP_SRC,
    Scenario,
    ScenarioStatuses,
    shared_statuses,
)
from nbkp.preflight.testkit.troubleshoot.snapshots import (
    BTRFS_MOUNT_ISSUES,
    DRY_RUN_PENDING,
    DST_LATEST_INVALID,
    DST_LATEST_MISSING,
    FS_TYPE_UNKNOWN,
    HARDLINK_ISSUES,
    LATEST_SNAPSHOT_WRITABLE,
    LATEST_UNREADABLE,
    SOURCE_LATEST_MISSING,
    SRC_DEVNULL_NO_UPSTREAM,
    SRC_LATEST_INVALID,
)

# In the order the syncs are listed by the troubleshoot output.
SCENARIOS: tuple[Scenario, ...] = (
    STANDALONE_ENDPOINTS,
    DISABLED_SYNC,
    UNAVAILABLE_VOLUMES,
    MISSING_SENTINELS,
    UNREADABLE_SENTINELS,
    RSYNC_MISSING,
    BTRFS_NOT_DETECTED,
    BTRFS_MOUNT_ISSUES,
    TOOLS_MISSING,
    HARDLINK_ISSUES,
    RSYNC_TOO_OLD,
    SOURCE_LATEST_MISSING,
    DRY_RUN_PENDING,
    LOCATION_EXCLUDED,
    BTRFS_PERMISSIONS,
    HARDLINK_PERMISSIONS,
    NO_SNAPSHOT_PERMISSIONS,
    MOUNT_ENCRYPTED,
    MOUNT_UNENCRYPTED,
    MOUNT_BTRFS_SUBVOLRM,
    MOUNT_DEVICE_MISSING,
    MOUNT_FSTAB_MISMATCH,
    MOUNT_LUKS_FAILED,
    MOUNT_MOUNT_FAILED,
    MOUNT_POLKIT_REFUSED,
    DST_LATEST_MISSING,
    DST_LATEST_INVALID,
    SRC_LATEST_INVALID,
    LATEST_UNREADABLE,
    SRC_DEVNULL_NO_UPSTREAM,
    HOST_TOOL_ERRORS,
    MOUNT_NO_PASSPHRASE,
    FS_TYPE_UNKNOWN,
    LATEST_SNAPSHOT_WRITABLE,
)


def troubleshoot_config() -> Config:
    """
    Return the base config plus every scenario's entries.

    Each sync needs a unique destination endpoint, and each endpoint a unique
    (volume, subdir) pair, so most scenarios bring a volume of their own.
    """
    return Config(
        ssh_endpoints={
            **base_ssh_endpoints(),
            **{ep.slug: ep for s in SCENARIOS for ep in s.ssh_endpoints},
        },
        volumes={
            **base_volumes(),
            **{vol.slug: vol for s in SCENARIOS for vol in s.volumes},
        },
        sync_endpoints={
            LAPTOP_SRC: SyncEndpoint(slug=LAPTOP_SRC, volume="laptop"),
            **{ep.slug: ep for s in SCENARIOS for ep in s.sync_endpoints},
        },
        syncs={sync.slug: sync for s in SCENARIOS for sync in s.syncs},
    )


def troubleshoot_data(config: Config) -> PreflightResult:
    """
    Return the statuses of every scenario.

    Against *config* from :func:`troubleshoot_config`.
    """
    shared = shared_statuses(config)
    results: list[ScenarioStatuses] = [s.statuses(config, shared) for s in SCENARIOS]
    vol_statuses = {
        vs.slug: vs
        for vs in (*shared.reported_volumes, *(v for r in results for v in r.volumes))
    }
    sync_statuses = {ss.slug: ss for r in results for ss in r.syncs}
    return assemble_preflight_result(
        vol_statuses,
        sync_statuses,
        # The troubleshoot output collects the other SSH statuses from the
        # statuses embedding them.
        ssh_endpoint_statuses={
            **{ssh.slug: ssh for r in results for ssh in r.standalone_ssh_endpoints},
            **demo_ssh_statuses(vol_statuses, sync_statuses),
        },
    )
