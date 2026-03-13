"""Fake check/troubleshoot data for manual testing."""

from __future__ import annotations

from ..fsprotocol import Snapshot
from . import (
    BtrfsSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeError,
    VolumeStatus,
)
from ..config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from ..config.testkit import (
    base_ssh_endpoints,
    base_sync_endpoints,
    base_syncs,
    base_volumes,
)


def check_config() -> Config:
    """Config with local + remote volumes and varied syncs.

    Includes orphan items to exercise the orphan-config warnings:
    - SSH endpoint ``orphan-server`` (not referenced by any volume)
    - Volume ``orphan-volume`` (not referenced by any sync endpoint)
    - Sync endpoint ``orphan-sync-endpoint`` (not referenced by any sync)
    """
    ssh_endpoints = base_ssh_endpoints()
    ssh_endpoints["orphan-server"] = SshEndpoint(
        slug="orphan-server",
        host="old.example.com",
        user="backup",
    )
    volumes = base_volumes()
    volumes["external-drive"] = LocalVolume(slug="external-drive", path="/mnt/external")
    volumes["orphan-volume"] = LocalVolume(slug="orphan-volume", path="/mnt/archive")
    sync_endpoints = base_sync_endpoints()
    sync_endpoints["external-root"] = SyncEndpoint(
        slug="external-root",
        volume="external-drive",
    )
    sync_endpoints["orphan-sync-endpoint"] = SyncEndpoint(
        slug="orphan-sync-endpoint",
        volume="usb-drive",
    )
    syncs = base_syncs()
    syncs["disabled-backup"] = SyncConfig(
        slug="disabled-backup",
        source="laptop-root",
        destination="external-root",
        enabled=False,
    )
    return Config(
        ssh_endpoints=ssh_endpoints,
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )


def check_data(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Volume and sync statuses with mixed active/inactive."""
    _local_caps = VolumeCapabilities(
        has_rsync=True,
        rsync_version_ok=True,
        has_btrfs=False,
        has_stat=True,
        has_findmnt=True,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
    )
    _usb_caps = VolumeCapabilities(
        has_rsync=True,
        rsync_version_ok=True,
        has_btrfs=True,
        has_stat=True,
        has_findmnt=True,
        is_btrfs_filesystem=True,
        hardlink_supported=True,
        btrfs_user_subvol_rm=True,
    )
    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        errors=[],
        capabilities=_local_caps,
    )
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        errors=[],
        capabilities=_usb_caps,
    )
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        errors=[VolumeError.UNREACHABLE],
    )
    external_vs = VolumeStatus(
        slug="external-drive",
        config=config.volumes["external-drive"],
        errors=[VolumeError.SENTINEL_NOT_FOUND],
    )

    vol_statuses = {
        "laptop": laptop_vs,
        "usb-drive": usb_vs,
        "nas-backup": nas_vs,
        "external-drive": external_vs,
    }

    sync_statuses = {
        "photos-to-usb": SyncStatus(
            slug="photos-to-usb",
            config=config.syncs["photos-to-usb"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            source_diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="laptop-photos",
                sentinel_exists=True,
            ),
            destination_diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="usb-photos",
                sentinel_exists=True,
                endpoint_writable=True,
                btrfs=BtrfsSubvolumeDiagnostics(
                    is_subvolume=True,
                    staging_dir_exists=True,
                    staging_dir_writable=True,
                ),
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="snapshots/2026-03-06T14:30:00.000Z",
                    target_valid=True,
                    snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
                ),
            ),
            destination_latest_snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
            errors=[],
        ),
        "docs-to-nas": SyncStatus(
            slug="docs-to-nas",
            config=config.syncs["docs-to-nas"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            errors=[SyncError.DESTINATION_UNAVAILABLE],
        ),
        "music-to-usb": SyncStatus(
            slug="music-to-usb",
            config=config.syncs["music-to-usb"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            source_diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="laptop-music",
                sentinel_exists=True,
            ),
            destination_diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="usb-music",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="/dev/null",
                ),
            ),
            errors=[],
        ),
        "disabled-backup": SyncStatus(
            slug="disabled-backup",
            config=config.syncs["disabled-backup"],
            source_status=laptop_vs,
            destination_status=external_vs,
            errors=[SyncError.DISABLED],
        ),
    }

    return vol_statuses, sync_statuses


def _troubleshoot_volumes() -> dict[str, LocalVolume]:
    """Extra local volumes for troubleshoot scenarios."""
    return {
        "usb-1": LocalVolume(slug="usb-1", path="/mnt/usb-1"),
        "usb-2": LocalVolume(slug="usb-2", path="/mnt/usb-2"),
        "usb-3": LocalVolume(slug="usb-3", path="/mnt/usb-3"),
        "usb-4": LocalVolume(slug="usb-4", path="/mnt/usb-4"),
        "usb-5": LocalVolume(slug="usb-5", path="/mnt/usb-5"),
        "usb-6": LocalVolume(slug="usb-6", path="/mnt/usb-6"),
        "usb-7": LocalVolume(slug="usb-7", path="/mnt/usb-7"),
        "usb-8": LocalVolume(slug="usb-8", path="/mnt/usb-8"),
        "usb-9": LocalVolume(slug="usb-9", path="/mnt/usb-9"),
    }


def troubleshoot_config() -> Config:
    """Config designed to trigger every troubleshoot error.

    Each sync needs a unique destination endpoint, and each
    endpoint needs a unique (volume, subdir) pair.  We use
    extra local volumes to satisfy these constraints.
    """
    base_vols = base_volumes()
    extra_vols = _troubleshoot_volumes()
    volumes = {
        **base_vols,
        **extra_vols,
        "home-nas": RemoteVolume(
            slug="home-nas",
            ssh_endpoint="home-only",
            path="/mnt/nas",
        ),
    }

    ssh_eps = base_ssh_endpoints()
    ssh_eps["home-only"] = SshEndpoint(
        slug="home-only",
        host="192.168.1.50",
        location="home",
    )

    sync_endpoints: dict[str, SyncEndpoint] = {
        # Source endpoints
        "laptop-src": SyncEndpoint(
            slug="laptop-src",
            volume="laptop",
        ),
        "usb-btrfs-src": SyncEndpoint(
            slug="usb-btrfs-src",
            volume="usb-drive",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        # Each sync gets its own unique destination endpoint
        "dst-disabled": SyncEndpoint(
            slug="dst-disabled",
            volume="usb-1",
        ),
        "dst-unavail": SyncEndpoint(
            slug="dst-unavail",
            volume="nas-backup",
        ),
        "dst-sentinels": SyncEndpoint(
            slug="dst-sentinels",
            volume="usb-2",
        ),
        "dst-rsync-missing": SyncEndpoint(
            slug="dst-rsync-missing",
            volume="nas-backup",
            subdir="rsync-check",
        ),
        "dst-btrfs-detect": SyncEndpoint(
            slug="dst-btrfs-detect",
            volume="usb-3",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        "dst-btrfs-mount": SyncEndpoint(
            slug="dst-btrfs-mount",
            volume="nas-backup",
            subdir="btrfs-mount",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        "dst-tools": SyncEndpoint(
            slug="dst-tools",
            volume="usb-4",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        "dst-hardlink": SyncEndpoint(
            slug="dst-hardlink",
            volume="usb-5",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=5),
        ),
        "dst-rsync-old": SyncEndpoint(
            slug="dst-rsync-old",
            volume="usb-6",
        ),
        "dst-src-latest": SyncEndpoint(
            slug="dst-src-latest",
            volume="nas-backup",
            subdir="src-latest",
        ),
        # Dry-run pending snapshot scenario: HL source with upstream
        "hl-stage": SyncEndpoint(
            slug="hl-stage",
            volume="usb-drive",
            subdir="stage",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        "dst-dry-run-pending": SyncEndpoint(
            slug="dst-dry-run-pending",
            volume="nas-backup",
            subdir="dry-run-pending",
        ),
        "dst-loc-excluded": SyncEndpoint(
            slug="dst-loc-excluded",
            volume="home-nas",
        ),
        "dst-btrfs-perms": SyncEndpoint(
            slug="dst-btrfs-perms",
            volume="usb-7",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
        "dst-hardlink-perms": SyncEndpoint(
            slug="dst-hardlink-perms",
            volume="usb-8",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        "dst-no-snap-perms": SyncEndpoint(
            slug="dst-no-snap-perms",
            volume="usb-9",
        ),
    }
    return Config(
        ssh_endpoints=ssh_eps,
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs={
            "disabled-sync": SyncConfig(
                slug="disabled-sync",
                source="laptop-src",
                destination="dst-disabled",
                enabled=False,
            ),
            "unavailable-volumes": SyncConfig(
                slug="unavailable-volumes",
                source="laptop-src",
                destination="dst-unavail",
            ),
            "missing-sentinels": SyncConfig(
                slug="missing-sentinels",
                source="laptop-src",
                destination="dst-sentinels",
            ),
            "rsync-missing": SyncConfig(
                slug="rsync-missing",
                source="laptop-src",
                destination="dst-rsync-missing",
            ),
            "btrfs-not-detected": SyncConfig(
                slug="btrfs-not-detected",
                source="laptop-src",
                destination="dst-btrfs-detect",
            ),
            "btrfs-mount-issues": SyncConfig(
                slug="btrfs-mount-issues",
                source="laptop-src",
                destination="dst-btrfs-mount",
            ),
            "tools-missing": SyncConfig(
                slug="tools-missing",
                source="laptop-src",
                destination="dst-tools",
            ),
            "hardlink-issues": SyncConfig(
                slug="hardlink-issues",
                source="laptop-src",
                destination="dst-hardlink",
            ),
            "rsync-too-old": SyncConfig(
                slug="rsync-too-old",
                source="laptop-src",
                destination="dst-rsync-old",
            ),
            "source-latest-missing": SyncConfig(
                slug="source-latest-missing",
                source="usb-btrfs-src",
                destination="dst-src-latest",
            ),
            # Upstream writes to hl-stage (HL snapshots)
            "dry-run-upstream": SyncConfig(
                slug="dry-run-upstream",
                source="laptop-src",
                destination="hl-stage",
            ),
            # Downstream reads from hl-stage; in dry-run,
            # latest → /dev/null because upstream didn't snapshot
            "dry-run-pending": SyncConfig(
                slug="dry-run-pending",
                source="hl-stage",
                destination="dst-dry-run-pending",
            ),
            "location-excluded": SyncConfig(
                slug="location-excluded",
                source="laptop-src",
                destination="dst-loc-excluded",
            ),
            "btrfs-permissions": SyncConfig(
                slug="btrfs-permissions",
                source="laptop-src",
                destination="dst-btrfs-perms",
            ),
            "hardlink-permissions": SyncConfig(
                slug="hardlink-permissions",
                source="laptop-src",
                destination="dst-hardlink-perms",
            ),
            "no-snap-permissions": SyncConfig(
                slug="no-snap-permissions",
                source="laptop-src",
                destination="dst-no-snap-perms",
            ),
        },
    )


def troubleshoot_data(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Statuses covering every VolumeError and SyncError."""
    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        errors=[VolumeError.SENTINEL_NOT_FOUND],
    )
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        errors=[],
    )
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        errors=[VolumeError.UNREACHABLE],
    )
    home_nas_vs = VolumeStatus(
        slug="home-nas",
        config=config.volumes["home-nas"],
        errors=[VolumeError.LOCATION_EXCLUDED],
    )
    usb7_vs = VolumeStatus(
        slug="usb-7",
        config=config.volumes["usb-7"],
        errors=[],
    )
    usb8_vs = VolumeStatus(
        slug="usb-8",
        config=config.volumes["usb-8"],
        errors=[],
    )
    usb9_vs = VolumeStatus(
        slug="usb-9",
        config=config.volumes["usb-9"],
        errors=[],
    )

    vol_statuses = {
        "laptop": laptop_vs,
        "usb-drive": usb_vs,
        "nas-backup": nas_vs,
        "home-nas": home_nas_vs,
        "usb-7": usb7_vs,
        "usb-8": usb8_vs,
        "usb-9": usb9_vs,
    }

    sync_statuses = {
        "disabled-sync": SyncStatus(
            slug="disabled-sync",
            config=config.syncs["disabled-sync"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[SyncError.DISABLED],
        ),
        "unavailable-volumes": SyncStatus(
            slug="unavailable-volumes",
            config=config.syncs["unavailable-volumes"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            errors=[
                SyncError.SOURCE_UNAVAILABLE,
                SyncError.DESTINATION_UNAVAILABLE,
            ],
        ),
        "missing-sentinels": SyncStatus(
            slug="missing-sentinels",
            config=config.syncs["missing-sentinels"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[
                SyncError.SOURCE_SENTINEL_NOT_FOUND,
                SyncError.DESTINATION_SENTINEL_NOT_FOUND,
            ],
        ),
        "rsync-missing": SyncStatus(
            slug="rsync-missing",
            config=config.syncs["rsync-missing"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            errors=[
                SyncError.SOURCE_RSYNC_NOT_FOUND,
                SyncError.DESTINATION_RSYNC_NOT_FOUND,
            ],
        ),
        "btrfs-not-detected": SyncStatus(
            slug="btrfs-not-detected",
            config=config.syncs["btrfs-not-detected"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[
                SyncError.DESTINATION_BTRFS_NOT_FOUND,
                SyncError.DESTINATION_NOT_BTRFS,
                SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME,
            ],
        ),
        "btrfs-mount-issues": SyncStatus(
            slug="btrfs-mount-issues",
            config=config.syncs["btrfs-mount-issues"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            errors=[
                SyncError.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM,
                SyncError.DESTINATION_TMP_NOT_FOUND,
                SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        "tools-missing": SyncStatus(
            slug="tools-missing",
            config=config.syncs["tools-missing"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[
                SyncError.DESTINATION_STAT_NOT_FOUND,
                SyncError.DESTINATION_FINDMNT_NOT_FOUND,
            ],
        ),
        "hardlink-issues": SyncStatus(
            slug="hardlink-issues",
            config=config.syncs["hardlink-issues"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[
                SyncError.DESTINATION_NO_HARDLINK_SUPPORT,
                SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        "rsync-too-old": SyncStatus(
            slug="rsync-too-old",
            config=config.syncs["rsync-too-old"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            errors=[
                SyncError.SOURCE_RSYNC_TOO_OLD,
                SyncError.DESTINATION_RSYNC_TOO_OLD,
            ],
        ),
        "source-latest-missing": SyncStatus(
            slug="source-latest-missing",
            config=config.syncs["source-latest-missing"],
            source_status=usb_vs,
            destination_status=nas_vs,
            errors=[
                SyncError.SOURCE_LATEST_NOT_FOUND,
                SyncError.SOURCE_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        "dry-run-upstream": SyncStatus(
            slug="dry-run-upstream",
            config=config.syncs["dry-run-upstream"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            errors=[],
        ),
        "dry-run-pending": SyncStatus(
            slug="dry-run-pending",
            config=config.syncs["dry-run-pending"],
            source_status=usb_vs,
            destination_status=nas_vs,
            errors=[SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING],
        ),
        "location-excluded": SyncStatus(
            slug="location-excluded",
            config=config.syncs["location-excluded"],
            source_status=laptop_vs,
            destination_status=home_nas_vs,
            errors=[SyncError.DESTINATION_UNAVAILABLE],
        ),
        "btrfs-permissions": SyncStatus(
            slug="btrfs-permissions",
            config=config.syncs["btrfs-permissions"],
            source_status=laptop_vs,
            destination_status=usb7_vs,
            errors=[
                SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE,
                SyncError.DESTINATION_STAGING_DIR_NOT_WRITABLE,
                SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE,
            ],
        ),
        "hardlink-permissions": SyncStatus(
            slug="hardlink-permissions",
            config=config.syncs["hardlink-permissions"],
            source_status=laptop_vs,
            destination_status=usb8_vs,
            errors=[
                SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE,
                SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE,
            ],
        ),
        "no-snap-permissions": SyncStatus(
            slug="no-snap-permissions",
            config=config.syncs["no-snap-permissions"],
            source_status=laptop_vs,
            destination_status=usb9_vs,
            errors=[
                SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE,
            ],
        ),
    }

    return vol_statuses, sync_statuses
