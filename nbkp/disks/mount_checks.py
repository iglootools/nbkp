"""Mount state probing: tool availability, config validation, runtime state.

Probes whether mount-related tools are installed and whether volumes
are currently mounted/attached.  Used by both ``disks status`` and
preflight checks.
"""

from __future__ import annotations

from ..config import (
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from .auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH
from . import direct as direct_cmds
from . import systemd as systemd_cmds
from .detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    resolve_mount_unit,
)
from .models import MountCapabilities, MountToolCapabilities
from .observation import MountObservation
from ..remote.queries import (
    _check_command_available,
    _check_file_exists,
    _check_systemctl_cat,
    _run_systemctl_show,
)
from ..remote.dispatch import run_on_volume


def probe_mount_tools(
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


def check_mount_capabilities(
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
    tools = mount_tools or probe_mount_tools(volume, re)
    return check_mount_capabilities(volume, mount, tools, re)


# ── Systemd backend ─────────────────────────────────────────


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


# ── Direct backend ──────────────────────────────────────────


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
