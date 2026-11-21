"""Scenarios about snapshot layout: filesystem support, snapshot directories,
and the ``latest`` symlink on either side of a sync."""

from ....config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from ....fsprotocol import Snapshot
from ... import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    DestinationEndpointWarningKind,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SyncErrorKind,
    SyncStatus,
)
from ..common import (
    LOCALHOST_SSH_STATUS,
    LOCALHOST_SSH_STATUS_BTRFS,
    active_dst_ep_status,
    active_src_ep_status,
    inactive_dst_ep_status,
)
from .scenario import (
    BOTH_ENDPOINTS_INACTIVE,
    LAPTOP_SRC,
    Scenario,
    ScenarioStatuses,
    SharedStatuses,
    broken_destination,
    healthy_caps,
    sync_from_laptop,
    volume_status,
)
from .ssh import BTRFS_HOST_TOOLS, nas_status

_HEALTHY_SNAPSHOT_DIRS = SnapshotDirsDiagnostics(exists=True, writable=True)
_LATEST_DEVNULL = LatestSymlinkState(exists=True, raw_target="/dev/null")
_LATEST_DANGLING = LatestSymlinkState(
    exists=True,
    raw_target="snapshots/2024-01-01T00:00:00.000Z",
    target_valid=False,
)


def _hard_link_endpoint(
    slug: str, volume: str, subdir: str | None = None
) -> SyncEndpoint:
    return SyncEndpoint(
        slug=slug,
        volume=volume,
        subdir=subdir,
        hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
    )


_NAS_BTRFS_MOUNT_SSH = nas_status(
    BTRFS_HOST_TOOLS, has_btrfs_endpoints=True, has_snapshot_endpoints=True
)


def _btrfs_mount_issues_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM + missing staging
    # + missing snapshots dir (Layer 3)
    nas = volume_status(
        config, "nas-backup", _NAS_BTRFS_MOUNT_SSH, healthy_caps(btrfs=True)
    )
    return ScenarioStatuses(
        syncs=(
            broken_destination(
                config,
                shared,
                "btrfs-mount-issues",
                nas,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-btrfs-mount",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    btrfs=BtrfsStagingSubvolumeDiagnostics(
                        staging_exists=False,
                        staging_is_subvolume=False,
                    ),
                    snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                    latest=_LATEST_DEVNULL,
                ),
                [
                    DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM,
                    DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND,
                    DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND,
                ],
            ),
        )
    )


BTRFS_MOUNT_ISSUES = Scenario(
    statuses=_btrfs_mount_issues_statuses,
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-btrfs-mount",
            volume="nas-backup",
            subdir="btrfs-mount",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("btrfs-mount-issues", "dst-btrfs-mount"),),
)


def _hardlink_issues_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT + missing snapshots dir
    # (Layer 3)
    usb5 = volume_status(
        config, "usb-5", LOCALHOST_SSH_STATUS, healthy_caps(hardlinks=False)
    )
    return ScenarioStatuses(
        syncs=(
            broken_destination(
                config,
                shared,
                "hardlink-issues",
                usb5,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-hardlink",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                    latest=_LATEST_DEVNULL,
                ),
                [
                    DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT,
                    DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND,
                ],
            ),
        )
    )


HARDLINK_ISSUES = Scenario(
    statuses=_hardlink_issues_statuses,
    volumes=(LocalVolume(slug="usb-5", path="/mnt/usb-5"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-hardlink",
            volume="usb-5",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=5),
        ),
    ),
    syncs=(sync_from_laptop("hardlink-issues", "dst-hardlink"),),
)


def _source_latest_missing_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND + SNAPSHOTS_DIR_NOT_FOUND
    # (Layer 3)
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="source-latest-missing",
                config=config.syncs["source-latest-missing"],
                source_endpoint_status=SourceEndpointStatus(
                    endpoint_slug="usb-btrfs-src",
                    volume_status=shared.usb_drive,
                    diagnostics=SourceEndpointDiagnostics(
                        endpoint_slug="usb-btrfs-src",
                        sentinel_exists=True,
                        snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                        latest=LatestSymlinkState(exists=False),
                    ),
                    errors=[
                        SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND,
                        SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND,
                    ],
                ),
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-src-latest", shared.nas_unreachable
                ),
                errors=list(BOTH_ENDPOINTS_INACTIVE),
            ),
        )
    )


SOURCE_LATEST_MISSING = Scenario(
    statuses=_source_latest_missing_statuses,
    sync_endpoints=(
        SyncEndpoint(
            slug="usb-btrfs-src",
            volume="usb-drive",
            # Subdir (not volume root) so it doesn't overlap the other usb-drive
            # source endpoints (hl-stage, src-latest-invalid, …).
            subdir="btrfs-src",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        SyncEndpoint(slug="dst-src-latest", volume="nas-backup", subdir="src-latest"),
    ),
    syncs=(
        SyncConfig(
            slug="source-latest-missing",
            source="usb-btrfs-src",
            destination="dst-src-latest",
        ),
    ),
)


def _dry_run_pending_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    upstream = SyncStatus(
        slug="dry-run-upstream",
        config=config.syncs["dry-run-upstream"],
        source_endpoint_status=shared.laptop_src_inactive,
        destination_endpoint_status=active_dst_ep_status(
            "hl-stage",
            shared.usb_drive,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="hl-stage",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
                latest=_LATEST_DEVNULL,
            ),
        ),
        errors=[SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
    )
    # SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING (Layer 4): in a dry run the
    # upstream sync takes no snapshot, so hl-stage's latest stays /dev/null.
    pending = SyncStatus(
        slug="dry-run-pending",
        config=config.syncs["dry-run-pending"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="hl-stage",
            volume_status=shared.usb_drive,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="hl-stage",
                sentinel_exists=True,
                snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
                latest=_LATEST_DEVNULL,
            ),
            errors=[],
        ),
        destination_endpoint_status=active_dst_ep_status(
            "dst-dry-run-pending", shared.nas_reachable
        ),
        errors=[SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING],
    )
    return ScenarioStatuses(syncs=(upstream, pending))


DRY_RUN_PENDING = Scenario(
    statuses=_dry_run_pending_statuses,
    sync_endpoints=(
        _hard_link_endpoint("hl-stage", "usb-drive", "stage"),
        SyncEndpoint(
            slug="dst-dry-run-pending", volume="nas-backup", subdir="dry-run-pending"
        ),
    ),
    syncs=(
        sync_from_laptop("dry-run-upstream", "hl-stage"),
        SyncConfig(
            slug="dry-run-pending",
            source="hl-stage",
            destination="dst-dry-run-pending",
        ),
    ),
)


def _dst_latest_missing_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    usb10 = volume_status(config, "usb-10", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            broken_destination(
                config,
                shared,
                "dst-latest-missing",
                usb10,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-latest-missing",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
                    latest=LatestSymlinkState(exists=False),
                ),
                [DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND],
            ),
        )
    )


DST_LATEST_MISSING = Scenario(
    statuses=_dst_latest_missing_statuses,
    volumes=(LocalVolume(slug="usb-10", path="/mnt/usb-10"),),
    sync_endpoints=(_hard_link_endpoint("dst-latest-missing", "usb-10"),),
    syncs=(sync_from_laptop("dst-latest-missing", "dst-latest-missing"),),
)


def _dst_latest_invalid_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    usb11 = volume_status(config, "usb-11", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            broken_destination(
                config,
                shared,
                "dst-latest-invalid",
                usb11,
                DestinationEndpointDiagnostics(
                    endpoint_slug="dst-latest-invalid",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
                    latest=_LATEST_DANGLING,
                ),
                [DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID],
            ),
        )
    )


DST_LATEST_INVALID = Scenario(
    statuses=_dst_latest_invalid_statuses,
    volumes=(LocalVolume(slug="usb-11", path="/mnt/usb-11"),),
    sync_endpoints=(_hard_link_endpoint("dst-latest-invalid", "usb-11"),),
    syncs=(sync_from_laptop("dst-latest-invalid", "dst-latest-invalid"),),
)


def _snapshot_source_status(
    shared: SharedStatuses,
    endpoint_slug: str,
    latest: LatestSymlinkState,
    errors: list[SourceEndpointErrorKind],
) -> SourceEndpointStatus:
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=shared.usb_drive,
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=endpoint_slug,
            sentinel_exists=True,
            snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
            latest=latest,
        ),
        errors=errors,
    )


def _src_latest_invalid_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # SourceEndpointErrorKind.LATEST_SYMLINK_INVALID on a source with no upstream
    usb13 = volume_status(config, "usb-13", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="src-latest-invalid",
                config=config.syncs["src-latest-invalid"],
                source_endpoint_status=_snapshot_source_status(
                    shared,
                    "src-latest-invalid",
                    _LATEST_DANGLING,
                    [SourceEndpointErrorKind.LATEST_SYMLINK_INVALID],
                ),
                destination_endpoint_status=active_dst_ep_status(
                    "dst-src-latest-invalid", usb13
                ),
                errors=[SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            ),
        )
    )


SRC_LATEST_INVALID = Scenario(
    statuses=_src_latest_invalid_statuses,
    volumes=(LocalVolume(slug="usb-13", path="/mnt/usb-13"),),
    sync_endpoints=(
        _hard_link_endpoint("src-latest-invalid", "usb-drive", "invalid-latest"),
        SyncEndpoint(slug="dst-src-latest-invalid", volume="usb-13"),
    ),
    syncs=(
        SyncConfig(
            slug="src-latest-invalid",
            source="src-latest-invalid",
            destination="dst-src-latest-invalid",
        ),
    ),
)


def _src_devnull_no_upstream_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM (Layer 4): the source's
    # latest → /dev/null, and no upstream sync will ever replace it.
    usb12 = volume_status(config, "usb-12", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="src-devnull-no-upstream",
                config=config.syncs["src-devnull-no-upstream"],
                source_endpoint_status=_snapshot_source_status(
                    shared, "src-devnull-no-upstream", _LATEST_DEVNULL, []
                ),
                destination_endpoint_status=active_dst_ep_status(
                    "dst-devnull-no-upstream", usb12
                ),
                errors=[SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM],
            ),
        )
    )


SRC_DEVNULL_NO_UPSTREAM = Scenario(
    statuses=_src_devnull_no_upstream_statuses,
    volumes=(LocalVolume(slug="usb-12", path="/mnt/usb-12"),),
    sync_endpoints=(
        _hard_link_endpoint("src-devnull-no-upstream", "usb-drive", "devnull-src"),
        SyncEndpoint(slug="dst-devnull-no-upstream", volume="usb-12"),
    ),
    syncs=(
        SyncConfig(
            slug="src-devnull-no-upstream",
            source="src-devnull-no-upstream",
            destination="dst-devnull-no-upstream",
        ),
    ),
)


def _fs_type_unknown_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # `stat -f` failed: DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN (Layer 3)
    usb15 = volume_status(
        config, "usb-15", LOCALHOST_SSH_STATUS_BTRFS, healthy_caps(btrfs=None)
    )
    return ScenarioStatuses(
        volumes=(usb15,),
        syncs=(
            SyncStatus(
                slug="fs-type-unknown",
                config=config.syncs["fs-type-unknown"],
                source_endpoint_status=active_src_ep_status(
                    LAPTOP_SRC, shared.laptop_active
                ),
                destination_endpoint_status=DestinationEndpointStatus(
                    endpoint_slug="dst-fs-unknown",
                    volume_status=usb15,
                    diagnostics=DestinationEndpointDiagnostics(
                        endpoint_slug="dst-fs-unknown",
                        sentinel_exists=True,
                        endpoint_writable=True,
                    ),
                    errors=[DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN],
                ),
                errors=[SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
            ),
        ),
    )


FS_TYPE_UNKNOWN = Scenario(
    statuses=_fs_type_unknown_statuses,
    volumes=(LocalVolume(slug="usb-15", path="/mnt/usb-15"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-fs-unknown",
            volume="usb-15",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("fs-type-unknown", "dst-fs-unknown"),),
)


_WRITABLE_SNAPSHOT = "2024-01-01T00:00:00.000Z"


def _latest_snapshot_writable_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE: a warning on an
    # active endpoint, so the sync stays active.
    usb16 = volume_status(
        config, "usb-16", LOCALHOST_SSH_STATUS_BTRFS, healthy_caps(btrfs=True)
    )
    return ScenarioStatuses(
        volumes=(usb16,),
        syncs=(
            SyncStatus(
                slug="latest-snapshot-writable",
                config=config.syncs["latest-snapshot-writable"],
                source_endpoint_status=active_src_ep_status(
                    LAPTOP_SRC, shared.laptop_active
                ),
                destination_endpoint_status=DestinationEndpointStatus(
                    endpoint_slug="dst-latest-writable",
                    volume_status=usb16,
                    diagnostics=DestinationEndpointDiagnostics(
                        endpoint_slug="dst-latest-writable",
                        sentinel_exists=True,
                        endpoint_writable=True,
                        btrfs=BtrfsStagingSubvolumeDiagnostics(
                            staging_exists=True,
                            staging_is_subvolume=True,
                            staging_writable=True,
                        ),
                        snapshot_dirs=_HEALTHY_SNAPSHOT_DIRS,
                        latest=LatestSymlinkState(
                            exists=True,
                            raw_target=f"snapshots/{_WRITABLE_SNAPSHOT}",
                            target_valid=True,
                            snapshot=Snapshot.from_name(_WRITABLE_SNAPSHOT),
                        ),
                        latest_snapshot_readonly=False,
                    ),
                    errors=[],
                    warnings=[DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE],
                ),
                errors=[],
            ),
        ),
    )


LATEST_SNAPSHOT_WRITABLE = Scenario(
    statuses=_latest_snapshot_writable_statuses,
    volumes=(LocalVolume(slug="usb-16", path="/mnt/usb-16"),),
    sync_endpoints=(
        SyncEndpoint(
            slug="dst-latest-writable",
            volume="usb-16",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(sync_from_laptop("latest-snapshot-writable", "dst-latest-writable"),),
)
