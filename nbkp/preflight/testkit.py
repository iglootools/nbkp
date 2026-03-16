"""Fake check/troubleshoot data for manual testing."""

from __future__ import annotations

from ..fsprotocol import Snapshot
from . import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    HostToolCapabilities,
    LatestSymlinkState,
    MountCapabilities,
    MountToolCapabilities,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SourceEndpointError,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from ..config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
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

# ── Shared helpers ────────────────────────────────────────────

_LOCALHOST_SSH_STATUS = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)

_LOCALHOST_SSH_STATUS_BTRFS = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=True,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)

_SENTINEL_MISSING_CAPS = VolumeCapabilities(
    sentinel_exists=False,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)


def _active_src_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
    *,
    snapshot_mode: bool = False,
) -> SourceEndpointStatus:
    """Build an active SourceEndpointStatus (sentinel exists, no errors)."""
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=endpoint_slug,
            sentinel_exists=True,
            **(
                {
                    "snapshot_dirs": SnapshotDirsDiagnostics(
                        exists=True, writable=True
                    ),
                    "latest": LatestSymlinkState(
                        exists=True,
                        raw_target="snapshots/2026-03-06T14:30:00.000Z",
                        target_valid=True,
                        snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
                    ),
                }
                if snapshot_mode
                else {}
            ),
        ),
        errors=[],
    )


def _active_dst_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
    diagnostics: DestinationEndpointDiagnostics | None = None,
) -> DestinationEndpointStatus:
    """Build an active DestinationEndpointStatus."""
    diag = diagnostics or DestinationEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=True,
        endpoint_writable=True,
    )
    return DestinationEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=diag,
        errors=[],
    )


def _inactive_dst_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
) -> DestinationEndpointStatus:
    """Build a DestinationEndpointStatus where volume is inactive."""
    return DestinationEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=None,
        errors=[],
    )


def _inactive_src_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
) -> SourceEndpointStatus:
    """Build a SourceEndpointStatus where volume is inactive."""
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=None,
        errors=[],
    )


# ── check_config / check_data ────────────────────────────────


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
                mapper_name="encrypted",
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
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
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

    # SSH endpoint statuses
    localhost_ssh = _LOCALHOST_SSH_STATUS
    localhost_ssh_btrfs = _LOCALHOST_SSH_STATUS_BTRFS

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
        errors=[],
    )
    external_vs = VolumeStatus(
        slug="external-drive",
        config=config.volumes["external-drive"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(capabilities=_SENTINEL_MISSING_CAPS),
        errors=[VolumeError.SENTINEL_NOT_FOUND],
    )
    # Encrypted volume: device present, luks not attached, not mounted
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
                    resolved_backend="systemd",
                    device_present=True,
                    luks_attached=False,
                    mounted=False,
                ),
            ),
        ),
        errors=[VolumeError.VOLUME_NOT_MOUNTED],
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
                    resolved_backend="systemd",
                    mount_unit="mnt-usb\\x2dbackup\\x2dmount.mount",
                    has_mount_unit_config=True,
                    has_polkit_rules=True,
                    device_present=True,
                    mounted=True,
                ),
            ),
        ),
        errors=[],
    )

    vol_statuses = {
        "laptop": laptop_vs,
        "usb-drive": usb_vs,
        "nas-backup": nas_vs,
        "external-drive": external_vs,
        "mount-encrypted": mount_encrypted_vs,
        "mount-unencrypted": mount_unencrypted_vs,
    }

    # Source/destination endpoint statuses for active syncs
    photos_src_ep = _active_src_ep_status("laptop-photos", laptop_vs)
    photos_dst_ep = _active_dst_ep_status(
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

    music_src_ep = _active_src_ep_status("laptop-music", laptop_vs)
    music_dst_ep = _active_dst_ep_status(
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

    laptop_root_src_ep = _active_src_ep_status("laptop-root", laptop_vs)

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
            source_endpoint_status=_inactive_src_ep_status("laptop-docs", laptop_vs),
            destination_endpoint_status=_inactive_dst_ep_status("nas-docs", nas_vs),
            errors=[],
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
            source_endpoint_status=_inactive_src_ep_status("laptop-root", laptop_vs),
            destination_endpoint_status=_inactive_dst_ep_status(
                "external-root", external_vs
            ),
            errors=[SyncError.DISABLED],
        ),
        "backup-to-encrypted": SyncStatus(
            slug="backup-to-encrypted",
            config=config.syncs["backup-to-encrypted"],
            source_endpoint_status=_inactive_src_ep_status("laptop-root", laptop_vs),
            destination_endpoint_status=_inactive_dst_ep_status(
                "mount-encrypted-dst", mount_encrypted_vs
            ),
            errors=[],
        ),
        "backup-to-unencrypted": SyncStatus(
            slug="backup-to-unencrypted",
            config=config.syncs["backup-to-unencrypted"],
            source_endpoint_status=laptop_root_src_ep,
            destination_endpoint_status=_active_dst_ep_status(
                "mount-unencrypted-dst",
                mount_unencrypted_vs,
            ),
            errors=[],
        ),
    }

    return vol_statuses, sync_statuses


# ── troubleshoot_config / troubleshoot_data ───────────────────


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
        "mount-encrypted": LocalVolume(
            slug="mount-encrypted",
            path="/mnt/encrypted",
            mount=MountConfig(
                device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                encryption=LuksEncryptionConfig(
                    mapper_name="encrypted",
                    passphrase_id="encrypted",
                ),
            ),
        ),
        "mount-unencrypted": LocalVolume(
            slug="mount-unencrypted",
            path="/mnt/usb-backup",
            mount=MountConfig(
                device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            ),
        ),
        "mount-direct": LocalVolume(
            slug="mount-direct",
            path="/mnt/direct-encrypted",
            mount=MountConfig(
                strategy="direct",
                device_uuid="dddddddd-eeee-ffff-0000-111111111111",
                encryption=LuksEncryptionConfig(
                    mapper_name="direct-encrypted",
                    passphrase_id="direct-encrypted",
                ),
            ),
        ),
        "mount-systemd-mismatch": LocalVolume(
            slug="mount-systemd-mismatch",
            path="/mnt/systemd-mismatch",
            mount=MountConfig(
                device_uuid="11111111-2222-3333-4444-555555555555",
                encryption=LuksEncryptionConfig(
                    mapper_name="systemd-mismatch",
                    passphrase_id="systemd-mismatch",
                ),
            ),
        ),
        "mount-luks-failed": LocalVolume(
            slug="mount-luks-failed",
            path="/mnt/luks-failed",
            mount=MountConfig(
                strategy="direct",
                device_uuid="66666666-7777-8888-9999-aaaaaaaaaaaa",
                encryption=LuksEncryptionConfig(
                    mapper_name="luks-failed",
                    passphrase_id="luks-failed",
                ),
            ),
        ),
        "usb-10": LocalVolume(slug="usb-10", path="/mnt/usb-10"),
        "usb-11": LocalVolume(slug="usb-11", path="/mnt/usb-11"),
        "usb-12": LocalVolume(slug="usb-12", path="/mnt/usb-12"),
        "usb-13": LocalVolume(slug="usb-13", path="/mnt/usb-13"),
    }


# ── Troubleshoot SSH endpoint statuses ────────────────────────

# localhost with all tools missing (for mount-encrypted scenario)
_TROUBLESHOOT_LOCALHOST_MOUNT_ENCRYPTED_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
        mount_tools=MountToolCapabilities(
            has_systemctl=False,
            has_systemd_escape=False,
            has_sudo=False,
            has_cryptsetup=False,
            has_systemd_cryptsetup=False,
        ),
    ),
    needs=SshEndpointToolNeeds(
        mount_systemd=True,
        has_encryption=True,
    ),
)

# localhost for mount-unencrypted (systemctl + systemd-escape present)
_TROUBLESHOOT_LOCALHOST_MOUNT_UNENCRYPTED_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
        mount_tools=MountToolCapabilities(
            has_systemctl=True,
            has_systemd_escape=True,
        ),
    ),
    needs=SshEndpointToolNeeds(mount_systemd=True),
)

# localhost for mount-direct (all direct tools missing)
_TROUBLESHOOT_LOCALHOST_MOUNT_DIRECT_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
        mount_tools=MountToolCapabilities(
            has_sudo=False,
            has_mount_cmd=False,
            has_umount_cmd=False,
            has_mountpoint=False,
            has_cryptsetup=False,
        ),
    ),
    needs=SshEndpointToolNeeds(
        mount_direct=True,
        has_encryption=True,
    ),
)

# NAS SSH endpoint: unreachable
_TROUBLESHOOT_NAS_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
)

# NAS SSH endpoint with rsync missing
_TROUBLESHOOT_NAS_RSYNC_MISSING_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(
        ssh_reachable=True,
        host_tools=HostToolCapabilities(
            has_rsync=False,
            rsync_version_ok=False,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)

# NAS SSH endpoint with rsync too old
_TROUBLESHOOT_NAS_RSYNC_OLD_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(
        ssh_reachable=True,
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=False,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)

# NAS SSH endpoint: reachable, with btrfs snapshots but mount issues
_TROUBLESHOOT_NAS_BTRFS_MOUNT_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(
        ssh_reachable=True,
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=True,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
    needs=SshEndpointToolNeeds(has_btrfs_endpoints=True, has_snapshot_endpoints=True),
)

# localhost with stat and findmnt missing (tools-missing scenario)
_TROUBLESHOOT_LOCALHOST_TOOLS_MISSING_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=False,
            has_findmnt=False,
        ),
    ),
    needs=SshEndpointToolNeeds(has_btrfs_endpoints=True, has_snapshot_endpoints=True),
)

# localhost with btrfs missing (btrfs-not-detected)
_TROUBLESHOOT_LOCALHOST_BTRFS_MISSING_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
    needs=SshEndpointToolNeeds(has_btrfs_endpoints=True, has_snapshot_endpoints=True),
)

# localhost for mount-systemd-mismatch (systemctl + systemd-escape present)
_TROUBLESHOOT_LOCALHOST_MOUNT_MISMATCH_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
        mount_tools=MountToolCapabilities(
            has_systemctl=True,
            has_systemd_escape=True,
            has_sudo=True,
            has_cryptsetup=True,
            has_systemd_cryptsetup=True,
        ),
    ),
    needs=SshEndpointToolNeeds(mount_systemd=True, has_encryption=True),
)

# localhost for mount-luks-failed (direct tools present)
_TROUBLESHOOT_LOCALHOST_LUKS_FAILED_SSH = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
        mount_tools=MountToolCapabilities(
            has_sudo=True,
            has_mount_cmd=True,
            has_umount_cmd=True,
            has_mountpoint=True,
            has_cryptsetup=True,
        ),
    ),
    needs=SshEndpointToolNeeds(mount_direct=True, has_encryption=True),
)

# home-nas: location excluded
_TROUBLESHOOT_HOME_NAS_SSH = SshEndpointStatus.from_diagnostics(
    slug="home-only",
    diagnostics=SshEndpointDiagnostics(location_excluded=True),
)

# NAS SSH endpoint for src-latest scenario (reachable but volume inactive)
_TROUBLESHOOT_NAS_REACHABLE_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(
        ssh_reachable=True,
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)


# ── Troubleshoot volume capabilities ─────────────────────────

_MOUNT_ENCRYPTED_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
    mount=MountCapabilities(
        resolved_backend="systemd",
        has_mount_unit_config=None,
        has_cryptsetup_service_config=None,
        has_polkit_rules=False,
        has_sudoers_rules=False,
        device_present=False,
        luks_attached=None,
        mounted=None,
    ),
)

_MOUNT_UNENCRYPTED_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
    mount=MountCapabilities(
        resolved_backend="systemd",
        mount_unit="mnt-usb\\x2dbackup.mount",
        has_mount_unit_config=False,
        has_polkit_rules=False,
        device_present=True,
        mounted=False,
    ),
)

_MOUNT_DIRECT_ENCRYPTED_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
    mount=MountCapabilities(
        resolved_backend="direct",
        has_sudoers_rules=False,
        device_present=False,
        luks_attached=None,
        mounted=None,
    ),
)

# mount-systemd-mismatch: device present, units configured but mismatched
_MOUNT_SYSTEMD_MISMATCH_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
    mount=MountCapabilities(
        resolved_backend="systemd",
        mount_unit="mnt-systemd\\x2dmismatch.mount",
        has_mount_unit_config=True,
        mount_unit_what="/dev/mapper/wrong-device",
        mount_unit_where="/mnt/wrong-path",
        has_cryptsetup_service_config=True,
        cryptsetup_service_exec_start="/usr/bin/cryptsetup attach wrong-mapper",
        has_polkit_rules=True,
        has_sudoers_rules=True,
        device_present=True,
        luks_attached=False,
        mounted=False,
    ),
)

# mount-luks-failed: device present, LUKS attach failed, passphrase unavailable
_MOUNT_LUKS_FAILED_CAPS = VolumeCapabilities(
    sentinel_exists=False,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
    mount=MountCapabilities(
        resolved_backend="direct",
        has_sudoers_rules=True,
        device_present=True,
        luks_attached=False,
        mounted=False,
    ),
)


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
        # Mount management endpoints
        "mount-encrypted-dst": SyncEndpoint(
            slug="mount-encrypted-dst",
            volume="mount-encrypted",
        ),
        "mount-unencrypted-dst": SyncEndpoint(
            slug="mount-unencrypted-dst",
            volume="mount-unencrypted",
        ),
        "mount-direct-dst": SyncEndpoint(
            slug="mount-direct-dst",
            volume="mount-direct",
        ),
        "mount-systemd-mismatch-dst": SyncEndpoint(
            slug="mount-systemd-mismatch-dst",
            volume="mount-systemd-mismatch",
        ),
        "mount-luks-failed-dst": SyncEndpoint(
            slug="mount-luks-failed-dst",
            volume="mount-luks-failed",
        ),
        # Symlink error endpoints
        "dst-latest-missing": SyncEndpoint(
            slug="dst-latest-missing",
            volume="usb-10",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        "dst-latest-invalid": SyncEndpoint(
            slug="dst-latest-invalid",
            volume="usb-11",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        # Source with invalid latest (standalone, no upstream)
        "src-latest-invalid": SyncEndpoint(
            slug="src-latest-invalid",
            volume="usb-drive",
            subdir="invalid-latest",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        # Source with latest → /dev/null and no upstream
        "src-devnull-no-upstream": SyncEndpoint(
            slug="src-devnull-no-upstream",
            volume="usb-drive",
            subdir="devnull-src",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        ),
        "dst-devnull-no-upstream": SyncEndpoint(
            slug="dst-devnull-no-upstream",
            volume="usb-12",
        ),
        "dst-src-latest-invalid": SyncEndpoint(
            slug="dst-src-latest-invalid",
            volume="usb-13",
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
            "mount-encrypted-errors": SyncConfig(
                slug="mount-encrypted-errors",
                source="laptop-src",
                destination="mount-encrypted-dst",
            ),
            "mount-unencrypted-errors": SyncConfig(
                slug="mount-unencrypted-errors",
                source="laptop-src",
                destination="mount-unencrypted-dst",
            ),
            "mount-direct-errors": SyncConfig(
                slug="mount-direct-errors",
                source="laptop-src",
                destination="mount-direct-dst",
            ),
            "mount-mismatch-errors": SyncConfig(
                slug="mount-mismatch-errors",
                source="laptop-src",
                destination="mount-systemd-mismatch-dst",
            ),
            "mount-luks-failed": SyncConfig(
                slug="mount-luks-failed",
                source="laptop-src",
                destination="mount-luks-failed-dst",
            ),
            "dst-latest-missing": SyncConfig(
                slug="dst-latest-missing",
                source="laptop-src",
                destination="dst-latest-missing",
            ),
            "dst-latest-invalid": SyncConfig(
                slug="dst-latest-invalid",
                source="laptop-src",
                destination="dst-latest-invalid",
            ),
            "src-latest-invalid": SyncConfig(
                slug="src-latest-invalid",
                source="src-latest-invalid",
                destination="dst-src-latest-invalid",
            ),
            "src-devnull-no-upstream": SyncConfig(
                slug="src-devnull-no-upstream",
                source="src-devnull-no-upstream",
                destination="dst-devnull-no-upstream",
            ),
        },
    )


def troubleshoot_data(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Statuses covering every VolumeError and SyncError.

    Errors are distributed across the 4-layer hierarchy:
    - SSH endpoint level: tool availability, reachability, location exclusion
    - Volume level: sentinel, mount config/state
    - Sync endpoint level: endpoint sentinel, dirs, symlinks, writability,
      capability-gated errors
    - Sync level: disabled, latest → /dev/null interpretation
    """

    # ── Layer 1: SSH endpoint statuses ────────────────────────
    # The troubleshoot output collects SSH statuses from volume statuses,
    # so we embed the appropriate SSH status in each volume.

    localhost_ssh = _LOCALHOST_SSH_STATUS
    localhost_ssh_btrfs = _LOCALHOST_SSH_STATUS_BTRFS

    # ── Layer 2: Volume statuses ──────────────────────────────

    # laptop: sentinel missing (Layer 2 error)
    laptop_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(capabilities=_SENTINEL_MISSING_CAPS),
        errors=[VolumeError.SENTINEL_NOT_FOUND],
    )

    # usb-drive: active (no errors)
    usb_vs = VolumeStatus(
        slug="usb-drive",
        config=config.volumes["usb-drive"],
        ssh_endpoint_status=localhost_ssh_btrfs,
        diagnostics=VolumeDiagnostics(),
        errors=[],
    )

    # nas-backup: SSH unreachable (Layer 1 error, volume has no own errors)
    nas_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=_TROUBLESHOOT_NAS_SSH,
        diagnostics=None,
        errors=[],
    )

    # home-nas: location excluded (Layer 1 error)
    home_nas_vs = VolumeStatus(
        slug="home-nas",
        config=config.volumes["home-nas"],
        ssh_endpoint_status=_TROUBLESHOOT_HOME_NAS_SSH,
        diagnostics=None,
        errors=[],
    )

    # usb-7 to usb-9: active for permissions tests
    usb7_vs = VolumeStatus(
        slug="usb-7",
        config=config.volumes["usb-7"],
        ssh_endpoint_status=localhost_ssh_btrfs,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=True,
                hardlink_supported=True,
                btrfs_user_subvol_rm=True,
            ),
        ),
        errors=[],
    )
    usb8_vs = VolumeStatus(
        slug="usb-8",
        config=config.volumes["usb-8"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )
    usb9_vs = VolumeStatus(
        slug="usb-9",
        config=config.volumes["usb-9"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # Mount management volumes: errors split across SSH endpoint and volume
    mount_encrypted_vs = VolumeStatus(
        slug="mount-encrypted",
        config=config.volumes["mount-encrypted"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_MOUNT_ENCRYPTED_SSH,
        diagnostics=VolumeDiagnostics(capabilities=_MOUNT_ENCRYPTED_CAPS),
        errors=[
            VolumeError.POLKIT_RULES_MISSING,
            VolumeError.SUDOERS_RULES_MISSING,
        ],
    )
    mount_unencrypted_vs = VolumeStatus(
        slug="mount-unencrypted",
        config=config.volumes["mount-unencrypted"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_MOUNT_UNENCRYPTED_SSH,
        diagnostics=VolumeDiagnostics(capabilities=_MOUNT_UNENCRYPTED_CAPS),
        errors=[
            VolumeError.MOUNT_UNIT_NOT_CONFIGURED,
            VolumeError.POLKIT_RULES_MISSING,
        ],
    )
    mount_direct_vs = VolumeStatus(
        slug="mount-direct",
        config=config.volumes["mount-direct"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_MOUNT_DIRECT_SSH,
        diagnostics=VolumeDiagnostics(capabilities=_MOUNT_DIRECT_ENCRYPTED_CAPS),
        errors=[
            VolumeError.SUDOERS_RULES_MISSING,
        ],
    )

    # mount-systemd-mismatch: unit mismatch + cryptsetup service mismatch (Layer 2)
    mount_mismatch_vs = VolumeStatus(
        slug="mount-systemd-mismatch",
        config=config.volumes["mount-systemd-mismatch"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_MOUNT_MISMATCH_SSH,
        diagnostics=VolumeDiagnostics(capabilities=_MOUNT_SYSTEMD_MISMATCH_CAPS),
        errors=[
            VolumeError.MOUNT_UNIT_MISMATCH,
            VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED,
            VolumeError.CRYPTSETUP_SERVICE_MISMATCH,
            VolumeError.VOLUME_NOT_MOUNTED,
        ],
    )

    # mount-luks-failed: LUKS attach failed + passphrase unavailable (Layer 2)
    mount_luks_failed_vs = VolumeStatus(
        slug="mount-luks-failed",
        config=config.volumes["mount-luks-failed"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_LUKS_FAILED_SSH,
        diagnostics=VolumeDiagnostics(capabilities=_MOUNT_LUKS_FAILED_CAPS),
        errors=[
            VolumeError.DEVICE_NOT_PRESENT,
            VolumeError.PASSPHRASE_NOT_AVAILABLE,
            VolumeError.ATTACH_LUKS_FAILED,
            VolumeError.MOUNT_FAILED,
        ],
    )

    # usb-10 to usb-12: active for symlink/devnull scenarios
    usb10_vs = VolumeStatus(
        slug="usb-10",
        config=config.volumes["usb-10"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )
    usb11_vs = VolumeStatus(
        slug="usb-11",
        config=config.volumes["usb-11"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )
    usb12_vs = VolumeStatus(
        slug="usb-12",
        config=config.volumes["usb-12"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    usb13_vs = VolumeStatus(
        slug="usb-13",
        config=config.volumes["usb-13"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # Volumes for rsync-missing scenario: NAS with rsync not found
    nas_rsync_missing_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=_TROUBLESHOOT_NAS_RSYNC_MISSING_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # Volume for rsync-too-old scenario
    nas_rsync_old_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=_TROUBLESHOOT_NAS_RSYNC_OLD_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # Volume for btrfs-mount-issues: btrfs FS but mount issues
    nas_btrfs_mount_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=_TROUBLESHOOT_NAS_BTRFS_MOUNT_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=True,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # usb-3: btrfs-not-detected (not btrfs filesystem + btrfs cmd missing)
    usb3_vs = VolumeStatus(
        slug="usb-3",
        config=config.volumes["usb-3"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_BTRFS_MISSING_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # usb-4: tools-missing (stat + findmnt missing)
    usb4_vs = VolumeStatus(
        slug="usb-4",
        config=config.volumes["usb-4"],
        ssh_endpoint_status=_TROUBLESHOOT_LOCALHOST_TOOLS_MISSING_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=False,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # usb-5: hardlink-issues (no hardlink support)
    usb5_vs = VolumeStatus(
        slug="usb-5",
        config=config.volumes["usb-5"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=False,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # usb-2: active (for missing-sentinels — endpoint sentinels missing)
    usb2_vs = VolumeStatus(
        slug="usb-2",
        config=config.volumes["usb-2"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )

    # NAS for src-latest and dry-run-pending (reachable, active)
    nas_reachable_vs = VolumeStatus(
        slug="nas-backup",
        config=config.volumes["nas-backup"],
        ssh_endpoint_status=_TROUBLESHOOT_NAS_REACHABLE_SSH,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
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
        "mount-encrypted": mount_encrypted_vs,
        "mount-unencrypted": mount_unencrypted_vs,
        "mount-direct": mount_direct_vs,
        "mount-systemd-mismatch": mount_mismatch_vs,
        "mount-luks-failed": mount_luks_failed_vs,
    }

    # ── Source endpoint status (shared by most syncs) ─────────
    # laptop-src: volume sentinel missing → source EP inactive (no diag)
    laptop_src_inactive = _inactive_src_ep_status("laptop-src", laptop_vs)

    # laptop-src with active volume (for syncs where laptop is active)
    # We need a version of laptop that's active for some scenarios
    laptop_active_vs = VolumeStatus(
        slug="laptop",
        config=config.volumes["laptop"],
        ssh_endpoint_status=localhost_ssh,
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )
    # ── Build sync statuses ───────────────────────────────────

    sync_statuses: dict[str, SyncStatus] = {}

    # disabled-sync: SyncError.DISABLED
    sync_statuses["disabled-sync"] = SyncStatus(
        slug="disabled-sync",
        config=config.syncs["disabled-sync"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status("dst-disabled", usb_vs),
        errors=[SyncError.DISABLED],
    )

    # unavailable-volumes: SSH unreachable on destination (Layer 1)
    # source vol sentinel missing (Layer 2)
    sync_statuses["unavailable-volumes"] = SyncStatus(
        slug="unavailable-volumes",
        config=config.syncs["unavailable-volumes"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status("dst-unavail", nas_vs),
        errors=[],
    )

    # missing-sentinels: endpoint sentinels missing (Layer 3)
    sync_statuses["missing-sentinels"] = SyncStatus(
        slug="missing-sentinels",
        config=config.syncs["missing-sentinels"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="laptop-src",
            volume_status=laptop_active_vs,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="laptop-src",
                sentinel_exists=False,
            ),
            errors=[SourceEndpointError.SENTINEL_NOT_FOUND],
        ),
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-sentinels",
            volume_status=usb2_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-sentinels",
                sentinel_exists=False,
                endpoint_writable=True,
            ),
            errors=[DestinationEndpointError.SENTINEL_NOT_FOUND],
        ),
        errors=[],
    )

    # rsync-missing: SshEndpointError.RSYNC_NOT_FOUND (Layer 1 on NAS)
    sync_statuses["rsync-missing"] = SyncStatus(
        slug="rsync-missing",
        config=config.syncs["rsync-missing"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "dst-rsync-missing", nas_rsync_missing_vs
        ),
        errors=[],
    )

    # btrfs-not-detected: SshEndpointError.BTRFS_NOT_FOUND (Layer 1)
    # + DestinationEndpointError.VOL_NOT_BTRFS (Layer 3)
    # + DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME (Layer 3)
    sync_statuses["btrfs-not-detected"] = SyncStatus(
        slug="btrfs-not-detected",
        config=config.syncs["btrfs-not-detected"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-btrfs-detect",
            volume_status=usb3_vs,
            diagnostics=DestinationEndpointDiagnostics(
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
                DestinationEndpointError.VOL_NOT_BTRFS,
                DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME,
            ],
        ),
        errors=[],
    )

    # btrfs-mount-issues: DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM
    # + missing staging + missing snapshots dir (Layer 3)
    sync_statuses["btrfs-mount-issues"] = SyncStatus(
        slug="btrfs-mount-issues",
        config=config.syncs["btrfs-mount-issues"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-btrfs-mount",
            volume_status=nas_btrfs_mount_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-btrfs-mount",
                sentinel_exists=True,
                endpoint_writable=True,
                btrfs=BtrfsStagingSubvolumeDiagnostics(
                    staging_exists=False,
                    staging_is_subvolume=False,
                ),
                snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
            ),
            errors=[
                DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM,
                DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND,
                DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        errors=[],
    )

    # tools-missing: SshEndpointError.STAT_NOT_FOUND + FINDMNT_NOT_FOUND (Layer 1)
    sync_statuses["tools-missing"] = SyncStatus(
        slug="tools-missing",
        config=config.syncs["tools-missing"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status("dst-tools", usb4_vs),
        errors=[],
    )

    # hardlink-issues: DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT
    # + missing snapshots dir (Layer 3)
    sync_statuses["hardlink-issues"] = SyncStatus(
        slug="hardlink-issues",
        config=config.syncs["hardlink-issues"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-hardlink",
            volume_status=usb5_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-hardlink",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
            ),
            errors=[
                DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT,
                DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
            ],
        ),
        errors=[],
    )

    # rsync-too-old: SshEndpointError.RSYNC_TOO_OLD (Layer 1 on NAS)
    sync_statuses["rsync-too-old"] = SyncStatus(
        slug="rsync-too-old",
        config=config.syncs["rsync-too-old"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "dst-rsync-old", nas_rsync_old_vs
        ),
        errors=[],
    )

    # source-latest-missing: SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
    # + SNAPSHOTS_DIR_NOT_FOUND (Layer 3)
    sync_statuses["source-latest-missing"] = SyncStatus(
        slug="source-latest-missing",
        config=config.syncs["source-latest-missing"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="usb-btrfs-src",
            volume_status=usb_vs,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="usb-btrfs-src",
                sentinel_exists=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
                latest=LatestSymlinkState(exists=False),
            ),
            errors=[
                SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
                SourceEndpointError.LATEST_SYMLINK_NOT_FOUND,
            ],
        ),
        destination_endpoint_status=_inactive_dst_ep_status("dst-src-latest", nas_vs),
        errors=[],
    )

    # dry-run-upstream: active (upstream writes to hl-stage)
    sync_statuses["dry-run-upstream"] = SyncStatus(
        slug="dry-run-upstream",
        config=config.syncs["dry-run-upstream"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_active_dst_ep_status(
            "hl-stage",
            usb_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="hl-stage",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
            ),
        ),
        errors=[],
    )

    # dry-run-pending: SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING (Layer 4)
    # Source reads from hl-stage which has latest → /dev/null
    sync_statuses["dry-run-pending"] = SyncStatus(
        slug="dry-run-pending",
        config=config.syncs["dry-run-pending"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="hl-stage",
            volume_status=usb_vs,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="hl-stage",
                sentinel_exists=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="/dev/null",
                ),
            ),
            errors=[],
        ),
        destination_endpoint_status=_active_dst_ep_status(
            "dst-dry-run-pending", nas_reachable_vs
        ),
        errors=[SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING],
    )

    # location-excluded: SshEndpointError.LOCATION_EXCLUDED (Layer 1)
    sync_statuses["location-excluded"] = SyncStatus(
        slug="location-excluded",
        config=config.syncs["location-excluded"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "dst-loc-excluded", home_nas_vs
        ),
        errors=[],
    )

    # btrfs-permissions: destination endpoint writability errors (Layer 3)
    sync_statuses["btrfs-permissions"] = SyncStatus(
        slug="btrfs-permissions",
        config=config.syncs["btrfs-permissions"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-btrfs-perms",
            volume_status=usb7_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-btrfs-perms",
                sentinel_exists=True,
                endpoint_writable=False,
                btrfs=BtrfsStagingSubvolumeDiagnostics(
                    staging_exists=True,
                    staging_is_subvolume=True,
                    staging_writable=False,
                ),
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=False),
                latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
            ),
            errors=[
                DestinationEndpointError.NOT_WRITABLE,
                DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE,
                DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE,
            ],
        ),
        errors=[],
    )

    # hardlink-permissions: destination endpoint writability errors (Layer 3)
    sync_statuses["hardlink-permissions"] = SyncStatus(
        slug="hardlink-permissions",
        config=config.syncs["hardlink-permissions"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-hardlink-perms",
            volume_status=usb8_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-hardlink-perms",
                sentinel_exists=True,
                endpoint_writable=False,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=False),
                latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
            ),
            errors=[
                DestinationEndpointError.NOT_WRITABLE,
                DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE,
            ],
        ),
        errors=[],
    )

    # no-snap-permissions: destination not writable (Layer 3)
    sync_statuses["no-snap-permissions"] = SyncStatus(
        slug="no-snap-permissions",
        config=config.syncs["no-snap-permissions"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-no-snap-perms",
            volume_status=usb9_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-no-snap-perms",
                sentinel_exists=True,
                endpoint_writable=False,
            ),
            errors=[
                DestinationEndpointError.NOT_WRITABLE,
            ],
        ),
        errors=[],
    )

    # mount-encrypted-errors: SSH endpoint has mount tool errors (Layer 1)
    # + volume has polkit/sudoers missing (Layer 2)
    sync_statuses["mount-encrypted-errors"] = SyncStatus(
        slug="mount-encrypted-errors",
        config=config.syncs["mount-encrypted-errors"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "mount-encrypted-dst", mount_encrypted_vs
        ),
        errors=[],
    )

    # mount-unencrypted-errors: volume has mount unit not configured (Layer 2)
    sync_statuses["mount-unencrypted-errors"] = SyncStatus(
        slug="mount-unencrypted-errors",
        config=config.syncs["mount-unencrypted-errors"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "mount-unencrypted-dst", mount_unencrypted_vs
        ),
        errors=[],
    )

    # mount-direct-errors: SSH endpoint has direct mount tool errors (Layer 1)
    # + volume has sudoers missing (Layer 2)
    sync_statuses["mount-direct-errors"] = SyncStatus(
        slug="mount-direct-errors",
        config=config.syncs["mount-direct-errors"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "mount-direct-dst", mount_direct_vs
        ),
        errors=[],
    )

    # mount-mismatch-errors: unit mismatch + cryptsetup service mismatch (Layer 2)
    sync_statuses["mount-mismatch-errors"] = SyncStatus(
        slug="mount-mismatch-errors",
        config=config.syncs["mount-mismatch-errors"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "mount-systemd-mismatch-dst", mount_mismatch_vs
        ),
        errors=[],
    )

    # mount-luks-failed: LUKS failures + passphrase unavailable (Layer 2)
    sync_statuses["mount-luks-failed"] = SyncStatus(
        slug="mount-luks-failed",
        config=config.syncs["mount-luks-failed"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=_inactive_dst_ep_status(
            "mount-luks-failed-dst", mount_luks_failed_vs
        ),
        errors=[],
    )

    # dst-latest-missing: DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND (Layer 3)
    sync_statuses["dst-latest-missing"] = SyncStatus(
        slug="dst-latest-missing",
        config=config.syncs["dst-latest-missing"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-latest-missing",
            volume_status=usb10_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-latest-missing",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(exists=False),
            ),
            errors=[DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND],
        ),
        errors=[],
    )

    # dst-latest-invalid: DestinationEndpointError.LATEST_SYMLINK_INVALID (Layer 3)
    sync_statuses["dst-latest-invalid"] = SyncStatus(
        slug="dst-latest-invalid",
        config=config.syncs["dst-latest-invalid"],
        source_endpoint_status=laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug="dst-latest-invalid",
            volume_status=usb11_vs,
            diagnostics=DestinationEndpointDiagnostics(
                endpoint_slug="dst-latest-invalid",
                sentinel_exists=True,
                endpoint_writable=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="snapshots/2024-01-01T00:00:00.000Z",
                    target_valid=False,
                ),
            ),
            errors=[DestinationEndpointError.LATEST_SYMLINK_INVALID],
        ),
        errors=[],
    )

    # src-latest-invalid: SourceEndpointError.LATEST_SYMLINK_INVALID (Layer 3)
    sync_statuses["src-latest-invalid"] = SyncStatus(
        slug="src-latest-invalid",
        config=config.syncs["src-latest-invalid"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="src-latest-invalid",
            volume_status=usb_vs,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="src-latest-invalid",
                sentinel_exists=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="snapshots/2024-01-01T00:00:00.000Z",
                    target_valid=False,
                ),
            ),
            errors=[SourceEndpointError.LATEST_SYMLINK_INVALID],
        ),
        destination_endpoint_status=_active_dst_ep_status(
            "dst-src-latest-invalid", usb13_vs
        ),
        errors=[],
    )

    # src-devnull-no-upstream: SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM (Layer 4)
    # Source has latest → /dev/null but no upstream sync writes to this endpoint
    sync_statuses["src-devnull-no-upstream"] = SyncStatus(
        slug="src-devnull-no-upstream",
        config=config.syncs["src-devnull-no-upstream"],
        source_endpoint_status=SourceEndpointStatus(
            endpoint_slug="src-devnull-no-upstream",
            volume_status=usb_vs,
            diagnostics=SourceEndpointDiagnostics(
                endpoint_slug="src-devnull-no-upstream",
                sentinel_exists=True,
                snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                latest=LatestSymlinkState(
                    exists=True,
                    raw_target="/dev/null",
                ),
            ),
            errors=[],
        ),
        destination_endpoint_status=_active_dst_ep_status(
            "dst-devnull-no-upstream", usb12_vs
        ),
        errors=[SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM],
    )

    return vol_statuses, sync_statuses
