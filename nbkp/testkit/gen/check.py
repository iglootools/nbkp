"""Fake check/troubleshoot data for manual testing."""

from __future__ import annotations

from ...check import (
    SyncReason,
    SyncStatus,
    VolumeReason,
    VolumeStatus,
)
from ...config import (
    BtrfsSnapshotConfig,
    Config,
    DestinationSyncEndpoint,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from .config import (
    base_ssh_endpoints,
    base_syncs,
    base_volumes,
)


def check_config() -> Config:
    """Config with local + remote volumes and varied syncs."""
    volumes = base_volumes()
    volumes["external-drive"] = LocalVolume(
        slug="external-drive", path="/mnt/external"
    )
    syncs = base_syncs()
    syncs["disabled-backup"] = SyncConfig(
        slug="disabled-backup",
        source=SyncEndpoint(volume="laptop"),
        destination=DestinationSyncEndpoint(
            volume="external-drive",
        ),
        enabled=False,
    )
    return Config(
        ssh_endpoints=base_ssh_endpoints(),
        volumes=volumes,
        syncs=syncs,
    )


def check_data(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Volume and sync statuses with mixed active/inactive."""
    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        reasons=[],
    )
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        reasons=[],
    )
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        reasons=[VolumeReason.UNREACHABLE],
    )
    external_vs = VolumeStatus(
        slug="external-drive",
        config=config.volumes["external-drive"],
        reasons=[VolumeReason.SENTINEL_NOT_FOUND],
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
            reasons=[],
        ),
        "docs-to-nas": SyncStatus(
            slug="docs-to-nas",
            config=config.syncs["docs-to-nas"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            reasons=[SyncReason.DESTINATION_UNAVAILABLE],
        ),
        "music-to-usb": SyncStatus(
            slug="music-to-usb",
            config=config.syncs["music-to-usb"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[],
        ),
        "disabled-backup": SyncStatus(
            slug="disabled-backup",
            config=config.syncs["disabled-backup"],
            source_status=laptop_vs,
            destination_status=external_vs,
            reasons=[SyncReason.DISABLED],
        ),
    }

    return vol_statuses, sync_statuses


def troubleshoot_config() -> Config:
    """Config designed to trigger every troubleshoot reason."""
    return Config(
        ssh_endpoints=base_ssh_endpoints(),
        volumes=base_volumes(),
        syncs={
            "disabled-sync": SyncConfig(
                slug="disabled-sync",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="usb-drive",
                ),
                enabled=False,
            ),
            "unavailable-volumes": SyncConfig(
                slug="unavailable-volumes",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="nas-backup",
                ),
            ),
            "missing-sentinels": SyncConfig(
                slug="missing-sentinels",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(volume="usb-drive"),
            ),
            "rsync-missing": SyncConfig(
                slug="rsync-missing",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(volume="nas-backup"),
            ),
            "btrfs-not-detected": SyncConfig(
                slug="btrfs-not-detected",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="usb-drive",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            ),
            "btrfs-mount-issues": SyncConfig(
                slug="btrfs-mount-issues",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="nas-backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            ),
            "tools-missing": SyncConfig(
                slug="tools-missing",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="usb-drive",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            ),
            "hardlink-issues": SyncConfig(
                slug="hardlink-issues",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="usb-drive",
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True, max_snapshots=5
                    ),
                ),
            ),
            "rsync-too-old": SyncConfig(
                slug="rsync-too-old",
                source=SyncEndpoint(volume="laptop"),
                destination=DestinationSyncEndpoint(
                    volume="nas-backup",
                ),
            ),
            "source-latest-missing": SyncConfig(
                slug="source-latest-missing",
                source=SyncEndpoint(
                    volume="usb-drive",
                    btrfs_snapshots=BtrfsSnapshotConfig(
                        enabled=True,
                    ),
                ),
                destination=DestinationSyncEndpoint(
                    volume="nas-backup",
                ),
            ),
        },
    )


def troubleshoot_data(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Statuses covering every VolumeReason and SyncReason."""
    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        reasons=[VolumeReason.SENTINEL_NOT_FOUND],
    )
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        reasons=[],
    )
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        reasons=[VolumeReason.UNREACHABLE],
    )

    vol_statuses = {
        "laptop": laptop_vs,
        "usb-drive": usb_vs,
        "nas-backup": nas_vs,
    }

    sync_statuses = {
        "disabled-sync": SyncStatus(
            slug="disabled-sync",
            config=config.syncs["disabled-sync"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[SyncReason.DISABLED],
        ),
        "unavailable-volumes": SyncStatus(
            slug="unavailable-volumes",
            config=config.syncs["unavailable-volumes"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            reasons=[
                SyncReason.SOURCE_UNAVAILABLE,
                SyncReason.DESTINATION_UNAVAILABLE,
            ],
        ),
        "missing-sentinels": SyncStatus(
            slug="missing-sentinels",
            config=config.syncs["missing-sentinels"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[
                SyncReason.SOURCE_SENTINEL_NOT_FOUND,
                SyncReason.DESTINATION_SENTINEL_NOT_FOUND,
            ],
        ),
        "rsync-missing": SyncStatus(
            slug="rsync-missing",
            config=config.syncs["rsync-missing"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            reasons=[
                SyncReason.RSYNC_NOT_FOUND_ON_SOURCE,
                SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION,
            ],
        ),
        "btrfs-not-detected": SyncStatus(
            slug="btrfs-not-detected",
            config=config.syncs["btrfs-not-detected"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[
                SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION,
                SyncReason.DESTINATION_NOT_BTRFS,
                SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME,
            ],
        ),
        "btrfs-mount-issues": SyncStatus(
            slug="btrfs-mount-issues",
            config=config.syncs["btrfs-mount-issues"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            reasons=[
                SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM,
                SyncReason.DESTINATION_TMP_NOT_FOUND,
                SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        "tools-missing": SyncStatus(
            slug="tools-missing",
            config=config.syncs["tools-missing"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[
                SyncReason.STAT_NOT_FOUND_ON_DESTINATION,
                SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION,
            ],
        ),
        "hardlink-issues": SyncStatus(
            slug="hardlink-issues",
            config=config.syncs["hardlink-issues"],
            source_status=laptop_vs,
            destination_status=usb_vs,
            reasons=[
                SyncReason.DESTINATION_NO_HARDLINK_SUPPORT,
                SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        "rsync-too-old": SyncStatus(
            slug="rsync-too-old",
            config=config.syncs["rsync-too-old"],
            source_status=laptop_vs,
            destination_status=nas_vs,
            reasons=[
                SyncReason.RSYNC_TOO_OLD_ON_SOURCE,
                SyncReason.RSYNC_TOO_OLD_ON_DESTINATION,
            ],
        ),
        "source-latest-missing": SyncStatus(
            slug="source-latest-missing",
            config=config.syncs["source-latest-missing"],
            source_status=usb_vs,
            destination_status=nas_vs,
            reasons=[
                SyncReason.SOURCE_LATEST_NOT_FOUND,
                SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
    }

    return vol_statuses, sync_statuses
