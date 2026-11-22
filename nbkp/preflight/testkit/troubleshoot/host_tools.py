"""Scenarios about tools missing or outdated on a host."""

from nbkp.config import BtrfsSnapshotConfig, Config, LocalVolume, SyncEndpoint
from nbkp.preflight import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SyncErrorKind,
    SyncStatus,
)
from nbkp.preflight.testkit.common import (
    active_dst_ep_status,
    active_src_ep_status,
    inactive_dst_ep_status,
)
from nbkp.preflight.testkit.troubleshoot.scenario import (
    LAPTOP_SRC,
    Scenario,
    ScenarioStatuses,
    SharedStatuses,
    broken_destination,
    healthy_caps,
    sync_from_laptop,
    volume_status,
)
from nbkp.preflight.testkit.troubleshoot.ssh import (
    BTRFS_SNAPSHOT_NEEDS,
    HOST_TOOLS,
    MOUNT_NEEDS,
    UDISKS_TOOLS,
    localhost_status,
    nas_status,
)

# rsync-missing, btrfs-not-detected, tools-missing and rsync-too-old keep the
# destination volume's errors empty although its SSH endpoint has tool errors,
# so the destination endpoint shows its own errors too.  In production,
# from_diagnostics() would short-circuit the volume to SSH_ENDPOINT_INACTIVE.

_NAS_RSYNC_MISSING_SSH = nas_status(
    HOST_TOOLS.model_copy(update={"has_rsync": False, "rsync_version_ok": False}),
)


def _rsync_missing_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    # SshEndpointErrorKind.RSYNC_NOT_FOUND (Layer 1 on NAS)
    nas = volume_status(config, "nas-backup", _NAS_RSYNC_MISSING_SSH, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="rsync-missing",
                config=config.syncs["rsync-missing"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-rsync-missing",
                    nas,
                ),
                errors=[SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            ),
        ),
    )


RSYNC_MISSING = Scenario(
    statuses=_rsync_missing_statuses,
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-rsync-missing",
            volume="nas-backup",
            subdir="rsync-check",
        ),
    ),
    syncs=(sync_from_laptop("rsync-missing", "dst-rsync-missing"),),
)

_LOCALHOST_BTRFS_MISSING_SSH = localhost_status(needs=BTRFS_SNAPSHOT_NEEDS)


def _btrfs_not_detected_statuses(
    config: Config,
    shared: SharedStatuses,
) -> ScenarioStatuses:
    # SshEndpointErrorKind.BTRFS_NOT_FOUND (Layer 1)
    # + DestinationEndpointErrorKind.VOL_NOT_BTRFS (Layer 3)
    # + DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME (Layer 3)
    usb3 = volume_status(config, "usb-3", _LOCALHOST_BTRFS_MISSING_SSH, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            broken_destination(
                config,
                shared,
                "btrfs-not-detected",
                usb3,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-btrfs-detect",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    btrfs=BtrfsStagingSubvolumeDiagnostics(
                        staging_exists=True,
                        staging_is_subvolume=False,
                    ),
                    snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                    latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
                ),
                errors=[
                    DestinationEndpointErrorKind.VOL_NOT_BTRFS,
                    DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME,
                ],
            ),
        ),
    )


BTRFS_NOT_DETECTED = Scenario(
    statuses=_btrfs_not_detected_statuses,
    volumes=(LocalVolume(slug="usb-3", path="/mnt/usb-3"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-btrfs-detect",
            volume="usb-3",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("btrfs-not-detected", "dst-btrfs-detect"),),
)

_LOCALHOST_TOOLS_MISSING_SSH = localhost_status(
    host_tools=HOST_TOOLS.model_copy(update={"has_stat": False, "has_findmnt": False}),
    needs=BTRFS_SNAPSHOT_NEEDS,
)


def _tools_missing_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    # SshEndpointErrorKind.STAT_NOT_FOUND + FINDMNT_NOT_FOUND (Layer 1)
    usb4 = volume_status(
        config,
        "usb-4",
        _LOCALHOST_TOOLS_MISSING_SSH,
        healthy_caps(hardlinks=False),
    )
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="tools-missing",
                config=config.syncs["tools-missing"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status("dst-tools", usb4),
                errors=[SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            ),
        ),
    )


TOOLS_MISSING = Scenario(
    statuses=_tools_missing_statuses,
    volumes=(LocalVolume(slug="usb-4", path="/mnt/usb-4"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-tools",
            volume="usb-4",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("tools-missing", "dst-tools"),),
)

_NAS_RSYNC_OLD_SSH = nas_status(
    HOST_TOOLS.model_copy(update={"rsync_version_ok": False}),
)


def _rsync_too_old_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    # SshEndpointErrorKind.RSYNC_TOO_OLD (Layer 1 on NAS)
    nas = volume_status(config, "nas-backup", _NAS_RSYNC_OLD_SSH, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="rsync-too-old",
                config=config.syncs["rsync-too-old"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-rsync-old",
                    nas,
                ),
                errors=[SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            ),
        ),
    )


RSYNC_TOO_OLD = Scenario(
    statuses=_rsync_too_old_statuses,
    volumes=(LocalVolume(slug="usb-6", path="/mnt/usb-6"),),
    sync_endpoints=(SyncEndpoint(slug="dst-rsync-old", volume="usb-6"),),
    syncs=(sync_from_laptop("rsync-too-old", "dst-rsync-old"),),
)

# localhost with lsblk missing while a mount-managed volume is configured
# (LSBLK_NOT_FOUND).  Localhost errors do not cascade to the volume; the
# syncs on it carry SyncErrorKind.ENDPOINT_HOST_ERRORS instead.
_LOCALHOST_LSBLK_MISSING_SSH = localhost_status(
    mount_tools=UDISKS_TOOLS.model_copy(update={"has_lsblk": False}),
    needs=MOUNT_NEEDS,
)


def _host_tool_errors_statuses(
    config: Config,
    shared: SharedStatuses,
) -> ScenarioStatuses:
    # SyncErrorKind.ENDPOINT_HOST_ERRORS (Layer 4; the fix lives at the SSH
    # endpoint layer)
    usb14 = volume_status(
        config,
        "usb-14",
        _LOCALHOST_LSBLK_MISSING_SSH,
        healthy_caps(),
    )
    return ScenarioStatuses(
        volumes=(usb14,),
        syncs=(
            SyncStatus(
                slug="host-tool-errors",
                config=config.syncs["host-tool-errors"],
                source_endpoint_status=active_src_ep_status(
                    LAPTOP_SRC,
                    shared.laptop_active,
                ),
                destination_endpoint_status=active_dst_ep_status(
                    "dst-host-errors",
                    usb14,
                ),
                errors=[SyncErrorKind.ENDPOINT_HOST_ERRORS],
            ),
        ),
    )


HOST_TOOL_ERRORS = Scenario(
    statuses=_host_tool_errors_statuses,
    volumes=(LocalVolume(slug="usb-14", path="/mnt/usb-14"),),
    sync_endpoints=(SyncEndpoint(slug="dst-host-errors", volume="usb-14"),),
    syncs=(sync_from_laptop("host-tool-errors", "dst-host-errors"),),
)
