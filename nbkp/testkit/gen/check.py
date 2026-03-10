"""Fake check/troubleshoot data for manual testing."""

from __future__ import annotations

from ...preflight import (
    SyncReason,
    SyncStatus,
    VolumeReason,
    VolumeStatus,
)
from ...config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from .config import (
    base_ssh_endpoints,
    base_sync_endpoints,
    base_syncs,
    base_volumes,
)


def check_config() -> Config:
    """Config with local + remote volumes and varied syncs."""
    volumes = base_volumes()
    volumes["external-drive"] = LocalVolume(slug="external-drive", path="/mnt/external")
    sync_endpoints = base_sync_endpoints()
    sync_endpoints["external-root"] = SyncEndpoint(
        slug="external-root",
        volume="external-drive",
    )
    syncs = base_syncs()
    syncs["disabled-backup"] = SyncConfig(
        slug="disabled-backup",
        source="laptop-root",
        destination="external-root",
        enabled=False,
    )
    return Config(
        ssh_endpoints=base_ssh_endpoints(),
        volumes=volumes,
        sync_endpoints=sync_endpoints,
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


def _troubleshoot_volumes() -> dict[str, LocalVolume]:
    """Extra local volumes for troubleshoot scenarios."""
    return {
        "usb-1": LocalVolume(slug="usb-1", path="/mnt/usb-1"),
        "usb-2": LocalVolume(slug="usb-2", path="/mnt/usb-2"),
        "usb-3": LocalVolume(slug="usb-3", path="/mnt/usb-3"),
        "usb-4": LocalVolume(slug="usb-4", path="/mnt/usb-4"),
        "usb-5": LocalVolume(slug="usb-5", path="/mnt/usb-5"),
        "usb-6": LocalVolume(slug="usb-6", path="/mnt/usb-6"),
    }


def troubleshoot_config() -> Config:
    """Config designed to trigger every troubleshoot reason.

    Each sync needs a unique destination endpoint, and each
    endpoint needs a unique (volume, subdir) pair.  We use
    extra local volumes to satisfy these constraints.
    """
    base_vols = base_volumes()
    extra_vols = _troubleshoot_volumes()
    volumes = {**base_vols, **extra_vols}

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
    }
    return Config(
        ssh_endpoints=base_ssh_endpoints(),
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
