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
from ..disks.mount_checks import (
    check_mount_capabilities as _check_mount_capabilities,
    probe_mount_tools as _probe_mount_tools,
)
from ..remote import run_remote_command
from .queries import (
    _check_command_available,
    _check_rsync_version,
)
from .snapshot_checks import (
    check_btrfs_filesystem,
    check_btrfs_mount_option,
    check_hardlink_support,
)
from ..disks.observation import MountObservation
from .status import (
    HostToolCapabilities,
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
