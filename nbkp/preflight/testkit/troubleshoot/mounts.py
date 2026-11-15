"""Scenarios about mount-managed volumes: udisks, fstab, LUKS, polkit."""

from ....config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    SyncEndpoint,
)
from ....disks import MountFailureReason, MountToolCapabilities
from ... import (
    BtrfsStagingSubvolumeDiagnostics,
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    LatestSymlinkState,
    MountCapabilities,
    SnapshotDirsDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SyncStatus,
    VolumeCapabilities,
    VolumeError,
)
from ..common import inactive_dst_ep_status
from .scenario import (
    BOTH_ENDPOINTS_INACTIVE,
    Scenario,
    ScenarioStatuses,
    SharedStatuses,
    broken_destination,
    sync_from_laptop,
    volume_status,
)
from .ssh import BTRFS_HOST_TOOLS, MOUNT_NEEDS, UDISKS_TOOLS, localhost_status

# udisks tools healthy: the errors are at the volume level
_LOCALHOST_UDISKS_SSH = localhost_status(mount_tools=UDISKS_TOOLS, needs=MOUNT_NEEDS)


def _encrypted_volume(slug: str, path: str, device_uuid: str) -> LocalVolume:
    """A LUKS volume whose passphrase id is its slug without ``mount-``."""
    return LocalVolume(
        slug=slug,
        path=path,
        mount=MountConfig(
            device_uuid=device_uuid,
            encryption=LuksEncryptionConfig(
                passphrase_id=slug.removeprefix("mount-"),
            ),
        ),
    )


def _mount_caps(mount: MountCapabilities, *, sentinel: bool) -> VolumeCapabilities:
    return VolumeCapabilities(
        sentinel_exists=sentinel,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=mount,
    )


def _unmounted_volume_scenario(
    sync_slug: str,
    volume: LocalVolume,
    ssh_status: SshEndpointStatus,
    capabilities: VolumeCapabilities,
    error: VolumeError,
) -> Scenario:
    """A sync from the inactive laptop to a volume that is not usable."""
    endpoint = f"{volume.slug}-dst"

    def statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
        vs = volume_status(config, volume.slug, ssh_status, capabilities, (error,))
        return ScenarioStatuses(
            volumes=(vs,),
            syncs=(
                SyncStatus(
                    slug=sync_slug,
                    config=config.syncs[sync_slug],
                    source_endpoint_status=shared.laptop_src_inactive,
                    destination_endpoint_status=inactive_dst_ep_status(endpoint, vs),
                    errors=list(BOTH_ENDPOINTS_INACTIVE),
                ),
            ),
        )

    return Scenario(
        statuses=statuses,
        volumes=(volume,),
        sync_endpoints=(SyncEndpoint(slug=endpoint, volume=volume.slug),),
        syncs=(sync_from_laptop(sync_slug, endpoint),),
    )


# In the mount-encrypted and mount-device-missing scenarios the SSH endpoint
# has mount tool errors (inactive) but the volume errors are kept for the
# demo; in production, from_diagnostics() would short-circuit the volume to
# SSH_ENDPOINT_INACTIVE only.

# udisksctl missing: the volume probe shows the device present but not
# unlocked/mounted (VOLUME_NOT_MOUNTED).
MOUNT_ENCRYPTED = _unmounted_volume_scenario(
    "mount-encrypted-errors",
    _encrypted_volume(
        "mount-encrypted", "/mnt/encrypted", "5941f273-f73c-44c5-a3ef-fae7248db1b6"
    ),
    localhost_status(
        mount_tools=MountToolCapabilities(
            has_udisksctl=False,
            has_findmnt=True,
            has_lsblk=True,
        ),
        needs=MOUNT_NEEDS,
    ),
    _mount_caps(
        MountCapabilities(
            has_fstab_entry=True,
            fstab_target="/mnt/encrypted",
            device_present=True,
            luks_unlocked=False,
            mounted=False,
        ),
        sentinel=True,
    ),
    VolumeError.VOLUME_NOT_MOUNTED,
)

# Device present but not mounted (VOLUME_NOT_MOUNTED).
MOUNT_UNENCRYPTED = _unmounted_volume_scenario(
    "mount-unencrypted-errors",
    LocalVolume(
        slug="mount-unencrypted",
        path="/mnt/usb-backup",
        mount=MountConfig(device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ),
    _LOCALHOST_UDISKS_SSH,
    _mount_caps(
        MountCapabilities(
            has_fstab_entry=True,
            fstab_target="/mnt/usb-backup",
            device_present=True,
            mounted=False,
        ),
        sentinel=True,
    ),
    VolumeError.VOLUME_NOT_MOUNTED,
)

# localhost: btrfs + udisks all present
_LOCALHOST_BTRFS_SUBVOLRM_SSH = localhost_status(
    host_tools=BTRFS_HOST_TOOLS,
    mount_tools=UDISKS_TOOLS.model_copy(update={"has_btrfs_module": True}),
    needs=SshEndpointToolNeeds(
        has_mount_volumes=True,
        has_btrfs_mount=True,
        has_btrfs_endpoints=True,
        has_snapshot_endpoints=True,
    ),
)


def _btrfs_subvolrm_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # The encrypted btrfs volume unlocks and mounts fine, but the live mount
    # lacks user_subvol_rm_allowed (needed for snapshot pruning).  The volume
    # itself is healthy; the missing option is a destination-endpoint error,
    # whose fix covers the udisks-managed branch (fstab + mount_options.conf).
    vs = volume_status(
        config,
        "mount-btrfs-subvolrm",
        _LOCALHOST_BTRFS_SUBVOLRM_SSH,
        VolumeCapabilities(
            sentinel_exists=True,
            is_btrfs_filesystem=True,
            hardlink_supported=True,
            btrfs_user_subvol_rm=False,
            mount=MountCapabilities(
                has_fstab_entry=True,
                fstab_target="/mnt/btrfs-subvolrm",
                device_present=True,
                luks_unlocked=True,
                mounted=True,
            ),
        ),
    )
    return ScenarioStatuses(
        volumes=(vs,),
        syncs=(
            broken_destination(
                config,
                shared,
                "mount-btrfs-subvolrm-missing",
                vs,
                DestinationEndpointDiagnostics(
                    endpoint_slug="mount-btrfs-subvolrm-dst",
                    sentinel_exists=True,
                    endpoint_writable=True,
                    btrfs=BtrfsStagingSubvolumeDiagnostics(
                        staging_exists=True,
                        staging_is_subvolume=True,
                        staging_writable=True,
                    ),
                    snapshot_dirs=SnapshotDirsDiagnostics(exists=True, writable=True),
                    latest=LatestSymlinkState(exists=True, raw_target="/dev/null"),
                ),
                [DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM],
            ),
        ),
    )


MOUNT_BTRFS_SUBVOLRM = Scenario(
    statuses=_btrfs_subvolrm_statuses,
    volumes=(
        _encrypted_volume(
            "mount-btrfs-subvolrm",
            "/mnt/btrfs-subvolrm",
            "99999999-aaaa-bbbb-cccc-dddddddddddd",
        ),
    ),
    sync_endpoints=(
        SyncEndpoint(
            slug="mount-btrfs-subvolrm-dst",
            volume="mount-btrfs-subvolrm",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    ),
    syncs=(
        sync_from_laptop("mount-btrfs-subvolrm-missing", "mount-btrfs-subvolrm-dst"),
    ),
)

# Device not plugged in (DEVICE_NOT_PRESENT), and udisksd not running.
MOUNT_DEVICE_MISSING = _unmounted_volume_scenario(
    "mount-device-missing",
    _encrypted_volume(
        "mount-device-missing",
        "/mnt/device-missing",
        "dddddddd-eeee-ffff-0000-111111111111",
    ),
    localhost_status(
        mount_tools=UDISKS_TOOLS.model_copy(update={"udisksd_running": False}),
        needs=MOUNT_NEEDS,
    ),
    _mount_caps(
        MountCapabilities(device_present=False, luks_unlocked=None, mounted=None),
        sentinel=False,
    ),
    VolumeError.DEVICE_NOT_PRESENT,
)

# Device present, but no fstab entry maps it to the configured path, so
# udisks would mount it under /run/media instead (FSTAB_MOUNTPOINT_MISMATCH).
MOUNT_FSTAB_MISMATCH = _unmounted_volume_scenario(
    "mount-fstab-mismatch",
    _encrypted_volume(
        "mount-fstab-mismatch",
        "/mnt/fstab-mismatch",
        "11111111-2222-3333-4444-555555555555",
    ),
    _LOCALHOST_UDISKS_SSH,
    _mount_caps(
        MountCapabilities(
            has_fstab_entry=False,
            device_present=True,
            luks_unlocked=True,
            mounted=False,
        ),
        sentinel=False,
    ),
    VolumeError.FSTAB_MOUNTPOINT_MISMATCH,
)


def _failed_mount_caps(path: str, reason: MountFailureReason) -> VolumeCapabilities:
    """The lifecycle reported *reason*, which preflight surfaces instead of
    VOLUME_NOT_MOUNTED."""
    return _mount_caps(
        MountCapabilities(
            has_fstab_entry=True,
            fstab_target=path,
            device_present=True,
            luks_unlocked=reason == MountFailureReason.MOUNT_FAILED,
            mounted=False,
            mount_failure_reason=reason,
        ),
        sentinel=False,
    )


MOUNT_LUKS_FAILED = _unmounted_volume_scenario(
    "mount-luks-failed",
    _encrypted_volume(
        "mount-luks-failed", "/mnt/luks-failed", "66666666-7777-8888-9999-aaaaaaaaaaaa"
    ),
    _LOCALHOST_UDISKS_SSH,
    _failed_mount_caps("/mnt/luks-failed", MountFailureReason.UNLOCK_FAILED),
    VolumeError.UNLOCK_FAILED,
)

# Unlocked, but the mount step failed.
MOUNT_MOUNT_FAILED = _unmounted_volume_scenario(
    "mount-mount-failed",
    _encrypted_volume(
        "mount-mount-failed",
        "/mnt/mount-failed",
        "77777777-8888-9999-aaaa-bbbbbbbbbbbb",
    ),
    _LOCALHOST_UDISKS_SSH,
    _failed_mount_caps("/mnt/mount-failed", MountFailureReason.MOUNT_FAILED),
    VolumeError.MOUNT_FAILED,
)

# udisks refused the action under --no-user-interaction because no polkit
# rule allows it.  The host also lacks the udisks btrfs module
# (SshEndpointWarning.UDISKS_BTRFS_MODULE_MISSING — a warning, not an error).
MOUNT_POLKIT_REFUSED = _unmounted_volume_scenario(
    "mount-polkit-refused",
    _encrypted_volume(
        "mount-polkit-refused",
        "/mnt/polkit-refused",
        "88888888-9999-aaaa-bbbb-cccccccccccc",
    ),
    localhost_status(
        host_tools=BTRFS_HOST_TOOLS,
        mount_tools=UDISKS_TOOLS.model_copy(update={"has_btrfs_module": False}),
        needs=SshEndpointToolNeeds(has_mount_volumes=True, has_btrfs_mount=True),
    ),
    _failed_mount_caps("/mnt/polkit-refused", MountFailureReason.NOT_AUTHORIZED),
    VolumeError.POLKIT_RULES_MISSING,
)

# Drive plugged in, but its passphrase cannot be retrieved.
MOUNT_NO_PASSPHRASE = _unmounted_volume_scenario(
    "mount-no-passphrase",
    _encrypted_volume(
        "mount-no-passphrase",
        "/mnt/no-passphrase",
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
    ),
    _LOCALHOST_UDISKS_SSH,
    _failed_mount_caps(
        "/mnt/no-passphrase", MountFailureReason.PASSPHRASE_NOT_AVAILABLE
    ),
    VolumeError.PASSPHRASE_NOT_AVAILABLE,
)
