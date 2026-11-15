"""Scenarios about destinations the backup user cannot write to (Layer 3)."""

from ....config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncEndpoint,
)
from ... import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
)
from ..common import LOCALHOST_SSH_STATUS, LOCALHOST_SSH_STATUS_BTRFS
from .scenario import (
    Scenario,
    ScenarioStatuses,
    SharedStatuses,
    broken_destination,
    healthy_caps,
    sync_from_laptop,
    volume_status,
)

_READ_ONLY_SNAPSHOT_DIRS = SnapshotDirsDiagnostics(exists=True, writable=False)
_LATEST_DEVNULL = LatestSymlinkState(exists=True, raw_target="/dev/null")


def _btrfs_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    usb7 = volume_status(
        config,
        "usb-7",
        LOCALHOST_SSH_STATUS_BTRFS,
        healthy_caps(btrfs=True, subvol_rm=True),
    )
    return ScenarioStatuses(
        volumes=(usb7,),
        syncs=(
            broken_destination(
                config,
                shared,
                "btrfs-permissions",
                usb7,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-btrfs-perms",
                    sentinel_exists=True,
                    endpoint_writable=False,
                    btrfs=BtrfsStagingSubvolumeDiagnostics(
                        staging_exists=True,
                        staging_is_subvolume=True,
                        staging_writable=False,
                    ),
                    snapshot_dirs=_READ_ONLY_SNAPSHOT_DIRS,
                    latest=_LATEST_DEVNULL,
                ),
                [
                    DestinationEndpointError.NOT_WRITABLE,
                    DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE,
                    DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE,
                ],
            ),
        ),
    )


BTRFS_PERMISSIONS = Scenario(
    statuses=_btrfs_statuses,
    volumes=(LocalVolume(slug="usb-7", path="/mnt/usb-7"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-btrfs-perms",
            volume="usb-7",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("btrfs-permissions", "dst-btrfs-perms"),),
)


def _hardlink_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    usb8 = volume_status(config, "usb-8", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        volumes=(usb8,),
        syncs=(
            broken_destination(
                config,
                shared,
                "hardlink-permissions",
                usb8,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-hardlink-perms",
                    sentinel_exists=True,
                    endpoint_writable=False,
                    snapshot_dirs=_READ_ONLY_SNAPSHOT_DIRS,
                    latest=_LATEST_DEVNULL,
                ),
                [
                    DestinationEndpointError.NOT_WRITABLE,
                    DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE,
                ],
            ),
        ),
    )


HARDLINK_PERMISSIONS = Scenario(
    statuses=_hardlink_statuses,
    volumes=(LocalVolume(slug="usb-8", path="/mnt/usb-8"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-hardlink-perms",
            volume="usb-8",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("hardlink-permissions", "dst-hardlink-perms"),),
)


def _no_snapshots_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    usb9 = volume_status(config, "usb-9", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        volumes=(usb9,),
        syncs=(
            broken_destination(
                config,
                shared,
                "no-snap-permissions",
                usb9,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-no-snap-perms",
                    sentinel_exists=True,
                    endpoint_writable=False,
                ),
                [DestinationEndpointError.NOT_WRITABLE],
            ),
        ),
    )


NO_SNAPSHOT_PERMISSIONS = Scenario(
    statuses=_no_snapshots_statuses,
    volumes=(LocalVolume(slug="usb-9", path="/mnt/usb-9"),),
    sync_endpoints=(SyncEndpoint(slug="dst-no-snap-perms", volume="usb-9"),),
    syncs=(sync_from_laptop("no-snap-permissions", "dst-no-snap-perms"),),
)
