"""Volume and SSH endpoint observation: raw state and capabilities.

Two levels of observation:

1. **SSH endpoint** — host reachability, host-level tool availability
   (rsync, btrfs, stat, findmnt), mount management tool availability
   (systemctl, sudo, cryptsetup, etc.).  Probed once per unique host.
2. **Volume** — sentinel existence, filesystem properties, mount
   config validation, and runtime mount state.  Probed once per volume
   whose SSH endpoint is active.

No error interpretation happens here — ``status.py`` translates
diagnostics into errors using ``SshEndpointToolNeeds`` and
``SyncEndpointStatus.from_diagnostics``.
"""

from __future__ import annotations

from pathlib import Path

from ..config import (
    LocalVolume,
    MountConfig,
    RemoteVolume,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..fsprotocol import VOLUME_SENTINEL
from ..mount.auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH
from ..mount import direct as direct_cmds
from ..mount import systemd as systemd_cmds
from ..mount.detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
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
    check_btrfs_filesystem,
    check_btrfs_mount_option,
    check_hardlink_support,
)
from ..mount.observation import MountObservation
from .status import (
    HostToolCapabilities,
    MountCapabilities,
    MountToolCapabilities,
    SshEndpointDiagnostics,
    VolumeCapabilities,
    VolumeDiagnostics,
)


# ── SSH Endpoint Observation ──────────────────────────────


def observe_ssh_endpoint(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
    probe_mount_tools: bool = False,
) -> SshEndpointDiagnostics:
    """Observe SSH endpoint state: reachability, host tools, mount tools.

    Uses *volume* as the dispatch target for running commands on the host.
    For local volumes, commands run locally (implicit localhost endpoint).
    For remote volumes, commands run over SSH via the resolved endpoint.

    *probe_mount_tools* should be True when any volume on this host has
    mount config.
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _observe_ssh_endpoint_local(volume, re, probe_mount_tools)
        case RemoteVolume():
            return _observe_ssh_endpoint_remote(volume, re, probe_mount_tools)


def _observe_ssh_endpoint_local(
    volume: LocalVolume,
    resolved_endpoints: ResolvedEndpoints,
    probe_mount_tools: bool,
) -> SshEndpointDiagnostics:
    """Observe implicit localhost SSH endpoint."""
    host_tools = _probe_host_tools(volume, resolved_endpoints)
    mount_tools = (
        _probe_mount_tools(volume, resolved_endpoints) if probe_mount_tools else None
    )
    return SshEndpointDiagnostics(
        host_tools=host_tools,
        mount_tools=mount_tools,
    )


def _observe_ssh_endpoint_remote(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
    probe_mount_tools: bool,
) -> SshEndpointDiagnostics:
    """Observe a remote SSH endpoint.

    SSH reachability is tested implicitly via the first host tool probe
    (``which rsync``).  If the SSH connection fails, the exception is
    caught and the host is marked unreachable.
    """
    if volume.slug not in resolved_endpoints:
        return SshEndpointDiagnostics(location_excluded=True)
    try:
        host_tools = _probe_host_tools(volume, resolved_endpoints)
        ssh_reachable = True
    except Exception:
        return SshEndpointDiagnostics(ssh_reachable=False)

    mount_tools = (
        _probe_mount_tools(volume, resolved_endpoints) if probe_mount_tools else None
    )
    return SshEndpointDiagnostics(
        ssh_reachable=ssh_reachable,
        host_tools=host_tools,
        mount_tools=mount_tools,
    )


def _probe_host_tools(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> HostToolCapabilities:
    """Probe host-level tool availability (rsync, btrfs, stat, findmnt).

    For remote volumes, any of these ``which`` probes implicitly tests
    SSH reachability — if the connection fails, the caller catches the
    exception.
    """
    has_rsync = _check_command_available(volume, "rsync", resolved_endpoints)
    rsync_version_ok = (
        _check_rsync_version(volume, resolved_endpoints) if has_rsync else False
    )
    has_btrfs = _check_command_available(volume, "btrfs", resolved_endpoints)
    has_stat = _check_command_available(volume, "stat", resolved_endpoints)
    has_findmnt = _check_command_available(volume, "findmnt", resolved_endpoints)
    return HostToolCapabilities(
        has_rsync=has_rsync,
        rsync_version_ok=rsync_version_ok,
        has_btrfs=has_btrfs,
        has_stat=has_stat,
        has_findmnt=has_findmnt,
    )


def _probe_mount_tools(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> MountToolCapabilities:
    """Probe mount management tool availability on the host.

    Probes all tools that might be needed by any volume on this host.
    Which tools are actually *required* is determined during error
    interpretation (``SshEndpointToolNeeds``).
    """
    has_systemctl = _check_command_available(volume, "systemctl", resolved_endpoints)
    has_systemd_escape = _check_command_available(
        volume, "systemd-escape", resolved_endpoints
    )
    has_sudo = _check_command_available(volume, "sudo", resolved_endpoints)
    has_cryptsetup = _check_command_available(volume, "cryptsetup", resolved_endpoints)
    cryptsetup_path = detect_systemd_cryptsetup_path(volume, resolved_endpoints)
    has_systemd_cryptsetup = cryptsetup_path is not None
    has_mount_cmd = _check_command_available(volume, "mount", resolved_endpoints)
    has_umount_cmd = _check_command_available(volume, "umount", resolved_endpoints)
    has_mountpoint = _check_command_available(volume, "mountpoint", resolved_endpoints)
    return MountToolCapabilities(
        has_systemctl=has_systemctl,
        has_systemd_escape=has_systemd_escape,
        has_systemd_cryptsetup=has_systemd_cryptsetup,
        systemd_cryptsetup_path=cryptsetup_path,
        has_sudo=has_sudo,
        has_cryptsetup=has_cryptsetup,
        has_mount_cmd=has_mount_cmd,
        has_umount_cmd=has_umount_cmd,
        has_mountpoint=has_mountpoint,
    )


# ── Volume Observation ────────────────────────────────────


def observe_volume(
    volume: Volume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observation: MountObservation | None = None,
) -> VolumeDiagnostics:
    """Observe volume state without interpreting errors.

    Returns a ``VolumeDiagnostics`` capturing raw observations:
    sentinel existence and capabilities.

    Called only when the SSH endpoint is active.  SSH reachability and
    location exclusion are handled at the SSH endpoint level.

    *host_tools* and *mount_tools* come from the SSH endpoint observation.
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _observe_local(
                volume, host_tools, mount_tools, re, mount_observation
            )
        case RemoteVolume():
            return _observe_remote(
                volume, host_tools, mount_tools, re, mount_observation
            )


def _observe_local(
    volume: LocalVolume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> VolumeDiagnostics:
    """Observe a local volume's state."""
    sentinel_exists = (Path(volume.path) / VOLUME_SENTINEL).exists()
    caps = (
        check_volume_capabilities(
            volume, host_tools, mount_tools, resolved_endpoints, mount_observation
        )
        if sentinel_exists
        else _sentinel_only_capabilities(
            volume, mount_tools, resolved_endpoints, mount_observation
        )
    )
    return VolumeDiagnostics(capabilities=caps)


def _observe_remote(
    volume: RemoteVolume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> VolumeDiagnostics:
    """Observe a remote volume's state.

    SSH reachability has already been verified at the SSH endpoint level.
    """
    ep = resolved_endpoints[volume.slug]
    sentinel_path = f"{volume.path}/{VOLUME_SENTINEL}"
    result = run_remote_command(
        ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
    )
    sentinel_exists = result.returncode == 0
    caps = (
        check_volume_capabilities(
            volume, host_tools, mount_tools, resolved_endpoints, mount_observation
        )
        if sentinel_exists
        else _sentinel_only_capabilities(
            volume, mount_tools, resolved_endpoints, mount_observation
        )
    )
    return VolumeDiagnostics(capabilities=caps)


def check_volume_capabilities(
    volume: Volume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observation: MountObservation | None = None,
) -> VolumeCapabilities:
    """Compute volume-level capabilities once per active volume.

    Host-level tool availability (rsync, btrfs, stat, findmnt) comes
    from *host_tools* (probed at the SSH endpoint level).  This function
    probes only volume-specific filesystem properties.
    """
    re = resolved_endpoints or {}
    is_btrfs = check_btrfs_filesystem(volume, re) if host_tools.has_stat else False
    hardlink_supported = (
        check_hardlink_support(volume, re) if host_tools.has_stat else True
    )
    btrfs_user_subvol_rm = (
        check_btrfs_mount_option(volume, "user_subvol_rm_allowed", re)
        if host_tools.has_findmnt and is_btrfs
        else False
    )

    mount_config: MountConfig | None = getattr(volume, "mount", None)
    mount_caps = (
        _check_mount_capabilities(
            volume, mount_config, mount_tools, re, mount_observation
        )
        if mount_config is not None
        else None
    )

    return VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=is_btrfs,
        hardlink_supported=hardlink_supported,
        btrfs_user_subvol_rm=btrfs_user_subvol_rm,
        mount=mount_caps,
    )


# ── Mount capabilities (volume-specific config + runtime state) ───


def _check_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> MountCapabilities:
    """Probe volume-specific mount config and runtime state.

    Tool availability (systemctl, sudo, etc.) comes from *mount_tools*
    at the SSH endpoint level.  This function probes only volume-specific
    config validation and runtime state.

    Dispatches to systemd or direct probing based on the configured
    strategy.  When *mount_observation* is available, it provides the
    resolved backend so the strategy resolution is skipped.
    """
    if mount_observation is not None:
        use_systemd = mount_observation.resolved_backend == "systemd"
    else:
        strategy = mount.strategy
        has_systemctl = (
            bool(mount_tools.has_systemctl) if mount_tools is not None else False
        )
        use_systemd = strategy == "systemd" or (strategy == "auto" and has_systemctl)
    if use_systemd:
        return _check_systemd_mount_capabilities(
            volume, mount, mount_tools, resolved_endpoints, mount_observation
        )
    else:
        return _check_direct_mount_capabilities(
            volume, mount, mount_tools, resolved_endpoints, mount_observation
        )


def _check_systemd_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> MountCapabilities:
    """Probe systemd-specific mount capabilities.

    Tool availability comes from *mount_tools*.  This function probes
    volume-specific config: mount unit configuration, cryptsetup service,
    auth rules, and runtime state.

    When *mount_observation* is provided, reuses ``mount_unit``,
    ``systemd_cryptsetup_path``, ``device_present``, ``luks_attached``,
    and ``mounted`` from the prior mount lifecycle — skipping the
    corresponding SSH round-trips.
    """
    obs = mount_observation
    has_systemctl = (
        bool(mount_tools.has_systemctl) if mount_tools is not None else False
    )
    has_systemd_escape = (
        bool(mount_tools.has_systemd_escape) if mount_tools is not None else False
    )

    # Derive mount unit via systemd-escape (or reuse from observation)
    mount_unit = (
        obs.mount_unit
        if obs is not None and obs.mount_unit is not None
        else resolve_mount_unit(volume, resolved_endpoints)
        if has_systemd_escape
        else None
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

    # Runtime mount state — reuse from observation when available
    device_present = (
        obs.device_present
        if obs is not None
        else detect_device_present(volume, mount.device_uuid, resolved_endpoints)
    )
    luks_attached = (
        obs.luks_attached
        if obs is not None
        else detect_luks_attached(
            volume, mount.encryption.mapper_name, resolved_endpoints
        )
        if mount.encryption is not None
        else None
    )
    mounted = (
        obs.mounted
        if obs is not None
        else run_on_volume(
            systemd_cmds.build_detect_mounted_command(mount_unit),
            volume,
            resolved_endpoints,
        ).returncode
        == 0
        if has_systemctl and mount_unit is not None
        else None
    )

    return MountCapabilities(
        resolved_backend="systemd",
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
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> MountCapabilities:
    """Probe direct-backend mount capabilities.

    Tool availability comes from *mount_tools*.  This function probes
    only volume-specific auth rules and runtime state.

    When *mount_observation* is provided, reuses ``device_present``,
    ``luks_attached``, and ``mounted`` from the prior mount lifecycle.
    """
    obs = mount_observation
    has_encryption = mount.encryption is not None
    has_mountpoint = (
        bool(mount_tools.has_mountpoint) if mount_tools is not None else False
    )

    has_sudoers_rules = (
        _check_file_exists(volume, SUDOERS_RULES_PATH, resolved_endpoints)
        if has_encryption
        else None
    )

    # Runtime mount state — reuse from observation when available
    device_present = (
        obs.device_present
        if obs is not None
        else detect_device_present(volume, mount.device_uuid, resolved_endpoints)
    )
    luks_attached = (
        obs.luks_attached
        if obs is not None
        else detect_luks_attached(
            volume, mount.encryption.mapper_name, resolved_endpoints
        )
        if mount.encryption is not None
        else None
    )
    mounted = (
        obs.mounted
        if obs is not None
        else run_on_volume(
            direct_cmds.build_detect_mounted_command(volume.path),
            volume,
            resolved_endpoints,
        ).returncode
        == 0
        if has_mountpoint
        else None
    )

    return MountCapabilities(
        resolved_backend="direct",
        has_sudoers_rules=has_sudoers_rules,
        device_present=device_present,
        luks_attached=luks_attached,
        mounted=mounted,
    )


def _sentinel_only_capabilities(
    volume: Volume,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
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
        _check_mount_capabilities(
            volume, mount_config, mount_tools, resolved_endpoints, mount_observation
        )
        if mount_config is not None
        else None
    )
    return VolumeCapabilities(
        sentinel_exists=False,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=mount_caps,
    )


def check_mount_status(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_tools: MountToolCapabilities | None = None,
) -> MountCapabilities:
    """Probe mount capabilities and runtime state for a single volume.

    Lightweight alternative to ``check_volume_capabilities`` — only
    probes mount-related capabilities (config validation and runtime
    device/luks/mounted state).

    When *mount_tools* is ``None``, probes mount tools on the fly.
    This is the case for standalone callers (e.g. ``volumes status``)
    that don't go through the full SSH endpoint observation path.
    """
    re = resolved_endpoints or {}
    tools = mount_tools or _probe_mount_tools(volume, re)
    return _check_mount_capabilities(volume, mount, tools, re)
