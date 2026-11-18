"""Fake ``preflight check`` data."""

from ...config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from ...config.testkit import (
    base_ssh_endpoints,
    base_sync_endpoints,
    base_syncs,
    base_volumes,
)
from ...fsprotocol import Snapshot
from .. import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    LatestSymlinkState,
    MountCapabilities,
    SnapshotDirsDiagnostics,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from ..output.formatting import collect_ssh_endpoint_statuses
from ..status.result import PreflightResult
from .common import (
    LOCALHOST_SSH_STATUS,
    LOCALHOST_SSH_STATUS_BTRFS,
    SENTINEL_MISSING_CAPS,
    active_dst_ep_status,
    active_src_ep_status,
    inactive_dst_ep_status,
    inactive_src_ep_status,
    standalone_ssh_status,
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
    volumes["mount-encrypted"] = LocalVolume(
        slug="mount-encrypted",
        path="/mnt/encrypted",
        mount=MountConfig(
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
            encryption=LuksEncryptionConfig(
                passphrase_id="encrypted",
            ),
        ),
    )
    volumes["mount-unencrypted"] = LocalVolume(
        slug="mount-unencrypted",
        path="/mnt/usb-backup-mount",
        mount=MountConfig(
            device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    )
    sync_endpoints = base_sync_endpoints()
    sync_endpoints["external-root"] = SyncEndpoint(
        slug="external-root",
        volume="external-drive",
    )
    sync_endpoints["orphan-sync-endpoint"] = SyncEndpoint(
        slug="orphan-sync-endpoint",
        volume="usb-drive",
        # Distinct subdir so it doesn't overlap usb-photos/usb-music (a root
        # endpoint would be nested over every subdir endpoint on usb-drive).
        # Its demo role is being orphaned (unreferenced by any sync), which a
        # subdir does not change.
        subdir="orphan-data",
    )
    sync_endpoints["mount-encrypted-dst"] = SyncEndpoint(
        slug="mount-encrypted-dst",
        volume="mount-encrypted",
    )
    sync_endpoints["mount-unencrypted-dst"] = SyncEndpoint(
        slug="mount-unencrypted-dst",
        volume="mount-unencrypted",
    )
    syncs = base_syncs()
    syncs["disabled-backup"] = SyncConfig(
        slug="disabled-backup",
        source="laptop-root",
        destination="external-root",
        enabled=False,
    )
    syncs["backup-to-encrypted"] = SyncConfig(
        slug="backup-to-encrypted",
        source="laptop-root",
        destination="mount-encrypted-dst",
    )
    syncs["backup-to-unencrypted"] = SyncConfig(
        slug="backup-to-unencrypted",
        source="laptop-root",
        destination="mount-unencrypted-dst",
    )
    return Config(
        ssh_endpoints=ssh_endpoints,
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )


def check_data(
    config: Config,
) -> PreflightResult:
    """Volume and sync statuses with mixed active/inactive."""
    _local_caps = VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
    )
    _usb_caps = VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=True,
        hardlink_supported=True,
        btrfs_user_subvol_rm=True,
    )

    localhost_ssh = LOCALHOST_SSH_STATUS
    localhost_ssh_btrfs = LOCALHOST_SSH_STATUS_BTRFS

    nas_ssh = SshEndpointStatus.from_diagnostics(
        slug="nas",
        diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
    )

    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(capabilities=_local_caps),
        errors=[],
    )
    laptop_system_vs = VolumeStatus(
        slug="laptop-system",
        config=config.volumes["laptop-system"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(capabilities=_local_caps),
        errors=[],
    )
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        ssh_endpoint_status=localhost_ssh_btrfs,
        diagnostics=VolumeDiagnostics(capabilities=_usb_caps),
        errors=[],
    )
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=nas_ssh,
        diagnostics=None,
        errors=[VolumeErrorKind.SSH_ENDPOINT_INACTIVE],
    )
    external_vs = VolumeStatus(
        slug="external-drive",
        config=config.volumes["external-drive"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(capabilities=SENTINEL_MISSING_CAPS),
        errors=[VolumeErrorKind.SENTINEL_NOT_FOUND],
    )
    # Encrypted volume: device present, luks not unlocked, not mounted
    mount_encrypted_vs = VolumeStatus(
        slug="mount-encrypted",
        config=config.volumes["mount-encrypted"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=False,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
                mount=MountCapabilities(
                    device_present=True,
                    luks_unlocked=False,
                    mounted=False,
                ),
            ),
        ),
        errors=[VolumeErrorKind.VOLUME_NOT_MOUNTED],
    )
    # Unencrypted volume: device present, mounted, active
    mount_unencrypted_vs = VolumeStatus(
        slug="mount-unencrypted",
        config=config.volumes["mount-unencrypted"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
                mount=MountCapabilities(
                    has_fstab_entry=True,
                    fstab_target="/mnt/usb-backup-mount",
                    device_present=True,
                    mounted=True,
                ),
            ),
        ),
        errors=[],
    )

    vol_statuses = {
        "laptop": laptop_vs,
        "laptop-system": laptop_system_vs,
        "usb-drive": usb_vs,
        "nas-backup": nas_vs,
        "external-drive": external_vs,
        "mount-encrypted": mount_encrypted_vs,
        "mount-unencrypted": mount_unencrypted_vs,
    }

    photos_src_ep = active_src_ep_status("laptop-photos", laptop_vs)
    photos_dst_ep = active_dst_ep_status(
        "usb-photos",
        usb_vs,
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug="usb-photos",
            sentinel_exists=True,
            endpoint_writable=True,
            btrfs=BtrfsStagingSubvolumeDiagnostics(
                staging_exists=True,
                staging_is_subvolume=True,
                staging_writable=True,
            ),
            snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
            latest=LatestSymlinkState(
                exists=True,
                raw_target="snapshots/2026-03-06T14:30:00.000Z",
                target_valid=True,
                snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
            ),
        ),
    )

    music_src_ep = active_src_ep_status("laptop-music", laptop_vs)
    music_dst_ep = active_dst_ep_status(
        "usb-music",
        usb_vs,
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug="usb-music",
            sentinel_exists=True,
            endpoint_writable=True,
            snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
            latest=LatestSymlinkState(
                exists=True,
                raw_target="/dev/null",
            ),
        ),
    )

    laptop_root_src_ep = active_src_ep_status("laptop-root", laptop_system_vs)

    sync_statuses = {
        "photos-to-usb": SyncStatus(
            slug="photos-to-usb",
            config=config.syncs["photos-to-usb"],
            source_endpoint_status=photos_src_ep,
            destination_endpoint_status=photos_dst_ep,
            destination_latest_snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
            errors=[],
        ),
        "docs-to-nas": SyncStatus(
            slug="docs-to-nas",
            config=config.syncs["docs-to-nas"],
            source_endpoint_status=inactive_src_ep_status("laptop-docs", laptop_vs),
            destination_endpoint_status=inactive_dst_ep_status("nas-docs", nas_vs),
            errors=[SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
        ),
        "music-to-usb": SyncStatus(
            slug="music-to-usb",
            config=config.syncs["music-to-usb"],
            source_endpoint_status=music_src_ep,
            destination_endpoint_status=music_dst_ep,
            errors=[],
        ),
        "disabled-backup": SyncStatus(
            slug="disabled-backup",
            config=config.syncs["disabled-backup"],
            source_endpoint_status=inactive_src_ep_status(
                "laptop-root", laptop_system_vs
            ),
            destination_endpoint_status=inactive_dst_ep_status(
                "external-root", external_vs
            ),
            errors=[SyncErrorKind.DISABLED],
        ),
        "backup-to-encrypted": SyncStatus(
            slug="backup-to-encrypted",
            config=config.syncs["backup-to-encrypted"],
            source_endpoint_status=inactive_src_ep_status(
                "laptop-root", laptop_system_vs
            ),
            destination_endpoint_status=inactive_dst_ep_status(
                "mount-encrypted-dst", mount_encrypted_vs
            ),
            errors=[SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
        ),
        "backup-to-unencrypted": SyncStatus(
            slug="backup-to-unencrypted",
            config=config.syncs["backup-to-unencrypted"],
            source_endpoint_status=laptop_root_src_ep,
            destination_endpoint_status=active_dst_ep_status(
                "mount-unencrypted-dst",
                mount_unencrypted_vs,
            ),
            errors=[],
        ),
    }

    ssh_statuses = {
        # Standalone endpoints: SSH reachable, no tool probing
        "bastion": standalone_ssh_status("bastion"),
        "bastion2": standalone_ssh_status("bastion2"),
        "nas-public": standalone_ssh_status("nas-public"),
        "orphan-server": standalone_ssh_status("orphan-server"),
        **collect_ssh_endpoint_statuses(vol_statuses, sync_statuses),
    }
    src_ep_statuses = {
        slug: ss.source_endpoint_status for slug, ss in sync_statuses.items()
    }
    dst_ep_statuses = {
        slug: ss.destination_endpoint_status for slug, ss in sync_statuses.items()
    }
    return PreflightResult(
        ssh_endpoint_statuses=ssh_statuses,
        volume_statuses=vol_statuses,
        source_endpoint_statuses=src_ep_statuses,
        destination_endpoint_statuses=dst_ep_statuses,
        sync_statuses=sync_statuses,
    )
