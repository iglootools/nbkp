"""Volume observation: raw state and capabilities without error interpretation."""

from __future__ import annotations

from pathlib import Path

from ..config import (
    LocalVolume,
    MountConfig,
    RemoteVolume,
    ResolvedEndpoints,
    Volume,
)
from ..fsprotocol import VOLUME_SENTINEL
from ..mount.auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH
from ..mount.detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    detect_volume_mounted,
    resolve_mount_unit,
)
from ..remote.dispatch import run_on_volume
from ..remote import run_remote_command
from .queries import (
    _check_command_available,
    _check_file_exists,
    _check_rsync_version,
    _check_systemctl_cat,
    _run_systemctl_show,
)
from .snapshot_checks import (
    _check_btrfs_filesystem,
    _check_btrfs_mount_option,
    _check_hardlink_support,
)
from .status import MountCapabilities, VolumeCapabilities, VolumeDiagnostics


def observe_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeDiagnostics:
    """Observe volume state without interpreting errors.

    Returns a ``VolumeDiagnostics`` capturing raw observations:
    sentinel existence, SSH reachability, and capabilities.
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _observe_local(volume, re)
        case RemoteVolume():
            return _observe_remote(volume, re)


def _observe_local(
    volume: LocalVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeDiagnostics:
    """Observe a local volume's state."""
    sentinel_exists = (Path(volume.path) / VOLUME_SENTINEL).exists()
    caps = (
        check_volume_capabilities(volume, resolved_endpoints)
        if sentinel_exists
        else _sentinel_only_capabilities(volume, resolved_endpoints)
    )
    return VolumeDiagnostics(
        capabilities=caps,
    )


def _observe_remote(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeDiagnostics:
    """Observe a remote volume's state."""
    if volume.slug not in resolved_endpoints:
        return VolumeDiagnostics(location_excluded=True)
    else:
        ep = resolved_endpoints[volume.slug]
        sentinel_path = f"{volume.path}/{VOLUME_SENTINEL}"
        try:
            result = run_remote_command(
                ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
            )
            ssh_reachable = True
            sentinel_exists = result.returncode == 0
        except Exception:
            ssh_reachable = False
            sentinel_exists = None
        caps = (
            check_volume_capabilities(volume, resolved_endpoints)
            if sentinel_exists
            else _sentinel_only_capabilities(volume, resolved_endpoints)
            if ssh_reachable
            else None
        )
        return VolumeDiagnostics(
            location_excluded=False,
            ssh_reachable=ssh_reachable,
            capabilities=caps,
        )


def check_volume_capabilities(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeCapabilities:
    """Compute host- and volume-level capabilities once per active volume."""
    has_rsync = _check_command_available(volume, "rsync", resolved_endpoints)
    rsync_version_ok = (
        _check_rsync_version(volume, resolved_endpoints) if has_rsync else False
    )
    has_btrfs = _check_command_available(volume, "btrfs", resolved_endpoints)
    has_stat = _check_command_available(volume, "stat", resolved_endpoints)
    has_findmnt = _check_command_available(volume, "findmnt", resolved_endpoints)
    is_btrfs = (
        _check_btrfs_filesystem(volume, resolved_endpoints) if has_stat else False
    )
    hardlink_supported = (
        _check_hardlink_support(volume, resolved_endpoints) if has_stat else True
    )
    btrfs_user_subvol_rm = (
        _check_btrfs_mount_option(volume, "user_subvol_rm_allowed", resolved_endpoints)
        if has_findmnt and is_btrfs
        else False
    )

    mount_config: MountConfig | None = getattr(volume, "mount", None)
    mount_caps = (
        _check_mount_capabilities(volume, mount_config, resolved_endpoints)
        if mount_config is not None
        else None
    )

    return VolumeCapabilities(
        sentinel_exists=True,
        has_rsync=has_rsync,
        rsync_version_ok=rsync_version_ok,
        has_btrfs=has_btrfs,
        has_stat=has_stat,
        has_findmnt=has_findmnt,
        is_btrfs_filesystem=is_btrfs,
        hardlink_supported=hardlink_supported,
        btrfs_user_subvol_rm=btrfs_user_subvol_rm,
        mount=mount_caps,
    )


def _check_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> MountCapabilities:
    """Probe mount-related capabilities for a volume with mount config.

    Dispatches to systemd or direct probing based on the configured
    strategy. ``auto`` probes for systemctl to decide.
    """
    strategy = mount.strategy
    use_systemd = strategy == "systemd" or (
        strategy == "auto"
        and _check_command_available(volume, "systemctl", resolved_endpoints)
    )
    if use_systemd:
        return _check_systemd_mount_capabilities(volume, mount, resolved_endpoints)
    else:
        return _check_direct_mount_capabilities(volume, mount, resolved_endpoints)


def _check_systemd_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> MountCapabilities:
    """Probe systemd-specific mount capabilities."""
    has_systemctl = _check_command_available(volume, "systemctl", resolved_endpoints)
    has_systemd_escape = _check_command_available(
        volume, "systemd-escape", resolved_endpoints
    )

    # Derive mount unit via systemd-escape
    mount_unit = (
        resolve_mount_unit(volume, resolved_endpoints) if has_systemd_escape else None
    )

    # Check mount unit config in systemd
    has_mount_unit_config = (
        _check_systemctl_cat(volume, mount_unit, resolved_endpoints)
        if has_systemctl and mount_unit is not None
        else None
    )
    mount_unit_props = (
        _run_systemctl_show(volume, mount_unit, ["What", "Where"], resolved_endpoints)
        if has_mount_unit_config and mount_unit is not None
        else {}
    )

    has_encryption = mount.encryption is not None

    # Encryption-specific checks — sudo is required for systemd-cryptsetup attach
    has_sudo = (
        _check_command_available(volume, "sudo", resolved_endpoints)
        if has_encryption
        else None
    )
    has_cryptsetup = (
        _check_command_available(volume, "cryptsetup", resolved_endpoints)
        if has_encryption
        else None
    )
    cryptsetup_path = (
        detect_systemd_cryptsetup_path(volume, resolved_endpoints)
        if has_encryption
        else None
    )
    has_systemd_cryptsetup = cryptsetup_path is not None if has_encryption else None

    # Cryptsetup service config check
    mapper_name = mount.encryption.mapper_name if mount.encryption else None
    cryptsetup_service = (
        f"systemd-cryptsetup@{mapper_name}.service" if mapper_name else None
    )
    has_cryptsetup_service_config = (
        _check_systemctl_cat(volume, cryptsetup_service, resolved_endpoints)
        if has_systemctl and cryptsetup_service is not None
        else None
    )
    cryptsetup_service_props = (
        _run_systemctl_show(
            volume, cryptsetup_service, ["ExecStart"], resolved_endpoints
        )
        if has_cryptsetup_service_config and cryptsetup_service is not None
        else {}
    )

    # Polkit and sudoers checks
    has_polkit_rules = _check_file_exists(
        volume,
        POLKIT_RULES_PATH,
        resolved_endpoints,
    )
    has_sudoers_rules = (
        _check_file_exists(volume, SUDOERS_RULES_PATH, resolved_endpoints)
        if has_encryption
        else None
    )

    # Runtime mount state
    device_present = detect_device_present(
        volume, mount.device_uuid, resolved_endpoints
    )
    luks_attached = (
        detect_luks_attached(volume, mount.encryption.mapper_name, resolved_endpoints)
        if mount.encryption is not None
        else None
    )
    mounted = (
        detect_volume_mounted(volume, mount_unit, resolved_endpoints)
        if has_systemctl and mount_unit is not None
        else None
    )

    return MountCapabilities(
        resolved_backend="systemd",
        has_systemctl=has_systemctl,
        has_systemd_escape=has_systemd_escape,
        has_sudo=has_sudo,
        has_cryptsetup=has_cryptsetup,
        has_systemd_cryptsetup=has_systemd_cryptsetup,
        systemd_cryptsetup_path=cryptsetup_path,
        mount_unit=mount_unit,
        has_mount_unit_config=has_mount_unit_config,
        mount_unit_what=mount_unit_props.get("What"),
        mount_unit_where=mount_unit_props.get("Where"),
        has_cryptsetup_service_config=has_cryptsetup_service_config,
        cryptsetup_service_exec_start=cryptsetup_service_props.get("ExecStart"),
        has_polkit_rules=has_polkit_rules,
        has_sudoers_rules=has_sudoers_rules,
        device_present=device_present,
        luks_attached=luks_attached,
        mounted=mounted,
    )


def _check_direct_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> MountCapabilities:
    """Probe direct-backend mount capabilities."""
    has_encryption = mount.encryption is not None

    has_sudo = _check_command_available(volume, "sudo", resolved_endpoints)
    has_mount_cmd = _check_command_available(volume, "mount", resolved_endpoints)
    has_umount_cmd = _check_command_available(volume, "umount", resolved_endpoints)
    has_mountpoint = _check_command_available(volume, "mountpoint", resolved_endpoints)
    has_cryptsetup = (
        _check_command_available(volume, "cryptsetup", resolved_endpoints)
        if has_encryption
        else None
    )
    has_sudoers_rules = (
        _check_file_exists(volume, SUDOERS_RULES_PATH, resolved_endpoints)
        if has_encryption
        else None
    )

    # Runtime mount state
    device_present = detect_device_present(
        volume, mount.device_uuid, resolved_endpoints
    )
    luks_attached = (
        detect_luks_attached(volume, mount.encryption.mapper_name, resolved_endpoints)
        if mount.encryption is not None
        else None
    )
    mounted = (
        run_on_volume(
            ["mountpoint", "-q", volume.path], volume, resolved_endpoints
        ).returncode
        == 0
        if has_mountpoint
        else None
    )

    return MountCapabilities(
        resolved_backend="direct",
        has_sudo=has_sudo,
        has_mount_cmd=has_mount_cmd,
        has_umount_cmd=has_umount_cmd,
        has_mountpoint=has_mountpoint,
        has_cryptsetup=has_cryptsetup,
        has_sudoers_rules=has_sudoers_rules,
        device_present=device_present,
        luks_attached=luks_attached,
        mounted=mounted,
    )


def _sentinel_only_capabilities(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeCapabilities:
    """Minimal capabilities for a reachable volume whose sentinel is missing.

    Only ``sentinel_exists`` is meaningful; the remaining fields are
    not probed and carry safe defaults.  Mount status is still probed
    when the volume has mount config — mount state is independent of
    the sentinel and represents a prerequisite for it (the drive must
    be mounted before the sentinel can exist).
    """
    mount_config: MountConfig | None = getattr(volume, "mount", None)
    mount_caps = (
        _check_mount_capabilities(volume, mount_config, resolved_endpoints)
        if mount_config is not None
        else None
    )
    return VolumeCapabilities(
        sentinel_exists=False,
        has_rsync=False,
        rsync_version_ok=False,
        has_btrfs=False,
        has_stat=False,
        has_findmnt=False,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=mount_caps,
    )


def check_mount_status(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> MountCapabilities:
    """Probe mount capabilities and runtime state for a single volume.

    Lightweight alternative to ``check_volume_capabilities`` — only
    probes mount-related capabilities (tool availability, config
    validation, and runtime device/luks/mounted state).
    """
    return _check_mount_capabilities(volume, mount, resolved_endpoints)
