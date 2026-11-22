"""Volume and SSH endpoint observation: raw state and capabilities.

Two levels of observation:

1. **SSH endpoint** — host reachability, host-level tool availability
   (rsync, btrfs, stat, findmnt), mount management tool availability
   (udisksctl + a running udisksd, lsblk, the udisks2-btrfs module).
   Probed once per unique host.
2. **Volume** — sentinel existence, filesystem properties, mount
   config validation, and runtime mount state.  Probed once per volume
   whose SSH endpoint is active.

No error interpretation happens here — the ``status`` package translates
diagnostics into errors using ``SshEndpointToolNeeds``,
``SshEndpointStatus.from_diagnostics`` and ``VolumeStatus.from_diagnostics``.

Every probe runs through *run* (:func:`remote.dispatch.run_on_volume`) and
finds local tools with *which* (:func:`shutil.which`), unless a test injects
fakes.
"""

import shutil
from pathlib import Path

from nbkp.config import LocalVolume, RemoteVolume, Volume
from nbkp.disks import (
    MountObservation,
    MountToolCapabilities,
    check_mount_capabilities as _check_mount_capabilities,
    probe_mount_tools as _probe_mount_tools,
)
from nbkp.fsprotocol import VOLUME_SENTINEL
from nbkp.preflight.probes import check_rsync_version
from nbkp.preflight.snapshot_checks import (
    check_btrfs_mount_option,
    is_btrfs_filesystem,
    probe_filesystem_type,
    supports_hard_links,
)
from nbkp.preflight.status.ssh import HostToolCapabilities, SshEndpointDiagnostics
from nbkp.preflight.status.volume import VolumeCapabilities, VolumeDiagnostics
from nbkp.remote.dispatch import (
    VolumeCommandRunner,
    WhichFn,
    check_command_available,
    run_on_volume,
)
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import describe_error, is_auth_error, ssh_connection_errors


def observe_ssh_endpoint(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
    probe_mount_tools: bool = False,
    *,
    run: VolumeCommandRunner = run_on_volume,
    which: WhichFn = shutil.which,
) -> SshEndpointDiagnostics:
    """Uses *volume* as the dispatch target for running commands on the host.
    For local volumes, commands run locally (implicit localhost endpoint).
    For remote volumes, commands run over SSH via the resolved endpoint.

    *probe_mount_tools* should be True when any volume on this host has
    mount config.
    """
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _observe_ssh_endpoint_local(
                volume, re, probe_mount_tools, run=run, which=which
            )
        case RemoteVolume():
            return _observe_ssh_endpoint_remote(
                volume, re, probe_mount_tools, run=run, which=which
            )


def _observe_ssh_endpoint_local(
    volume: LocalVolume,
    resolved_endpoints: ResolvedEndpoints,
    probe_mount_tools: bool,
    *,
    run: VolumeCommandRunner,
    which: WhichFn,
) -> SshEndpointDiagnostics:
    host_tools = _probe_host_tools(volume, resolved_endpoints, run=run, which=which)
    mount_tools = (
        _probe_mount_tools(volume, resolved_endpoints, run=run, which=which)
        if probe_mount_tools
        else None
    )
    return SshEndpointDiagnostics(
        host_tools=host_tools,
        mount_tools=mount_tools,
    )


def _observe_ssh_endpoint_remote(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
    probe_mount_tools: bool,
    *,
    run: VolumeCommandRunner,
    which: WhichFn,
) -> SshEndpointDiagnostics:
    """SSH reachability is tested implicitly via the first host tool probe
    (``which rsync``).  A connection failure — then or during a later probe,
    each of which connects anew — marks the host unreachable (or
    auth-failed) and records the cause; any other exception is a bug and
    propagates.
    """
    if volume.slug not in resolved_endpoints:
        return SshEndpointDiagnostics(location_excluded=True)
    try:
        return SshEndpointDiagnostics(
            ssh_reachable=True,
            host_tools=_probe_host_tools(
                volume, resolved_endpoints, run=run, which=which
            ),
            mount_tools=(
                _probe_mount_tools(volume, resolved_endpoints, run=run, which=which)
                if probe_mount_tools
                else None
            ),
        )
    except ssh_connection_errors() as e:
        return unreachable_diagnostics(e)


def unreachable_diagnostics(error: BaseException) -> SshEndpointDiagnostics:
    return SshEndpointDiagnostics(
        ssh_reachable=False,
        ssh_auth_failed=is_auth_error(error),
        ssh_error=describe_error(error),
    )


def _probe_host_tools(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
    which: WhichFn,
) -> HostToolCapabilities:
    """For remote volumes, any of these ``which`` probes implicitly tests
    SSH reachability — if the connection fails, the caller catches the
    exception.
    """

    def available(command: str) -> bool:
        return check_command_available(
            volume, command, resolved_endpoints, run=run, which=which
        )

    has_rsync = available("rsync")
    rsync_version_ok = (
        check_rsync_version(volume, resolved_endpoints, run=run) if has_rsync else False
    )
    has_btrfs = available("btrfs")
    has_stat = available("stat")
    has_findmnt = available("findmnt")
    return HostToolCapabilities(
        has_rsync=has_rsync,
        rsync_version_ok=rsync_version_ok,
        has_btrfs=has_btrfs,
        has_stat=has_stat,
        has_findmnt=has_findmnt,
    )


def observe_volume(
    volume: Volume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observation: MountObservation | None = None,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> VolumeDiagnostics:
    """Called only when the SSH endpoint is active.  SSH reachability and
    location exclusion are handled at the SSH endpoint level.

    *host_tools* and *mount_tools* come from the SSH endpoint observation.
    """
    re = resolved_endpoints or {}
    caps = (
        check_volume_capabilities(
            volume, host_tools, mount_tools, re, mount_observation, run=run
        )
        if _volume_sentinel_exists(volume, re, run=run)
        else _sentinel_only_capabilities(
            volume, mount_tools, re, mount_observation, run=run
        )
    )
    return VolumeDiagnostics(capabilities=caps)


def _volume_sentinel_exists(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> bool:
    """A mount-managed volume with no declared path that is not yet mounted
    has no knowable location — treated as sentinel-missing (inactive).

    For a remote volume, SSH reachability has already been verified at the
    SSH endpoint level.
    """
    if volume.path is None:
        return False
    sentinel_path = f"{volume.path}/{VOLUME_SENTINEL}"
    match volume:
        case LocalVolume():
            return Path(sentinel_path).is_file()
        case RemoteVolume():
            return (
                run(
                    ["test", "-f", sentinel_path], volume, resolved_endpoints
                ).returncode
                == 0
            )


def check_volume_capabilities(
    volume: Volume,
    host_tools: HostToolCapabilities,
    mount_tools: MountToolCapabilities | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observation: MountObservation | None = None,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> VolumeCapabilities:
    """Compute volume-level capabilities once per active volume.

    Host-level tool availability (rsync, btrfs, stat, findmnt) comes
    from *host_tools* (probed at the SSH endpoint level).  This function
    probes only volume-specific filesystem properties.
    """
    re = resolved_endpoints or {}
    fs_type = (
        probe_filesystem_type(volume, re, run=run) if host_tools.has_stat else None
    )
    is_btrfs = is_btrfs_filesystem(fs_type) if host_tools.has_stat else False
    hardlink_supported = supports_hard_links(fs_type)
    btrfs_user_subvol_rm = (
        check_btrfs_mount_option(volume, "user_subvol_rm_allowed", re, run=run)
        if host_tools.has_findmnt and is_btrfs
        else False
    )

    mount_caps = (
        _check_mount_capabilities(
            volume, volume.mount, mount_tools, re, mount_observation, run=run
        )
        if volume.mount is not None
        else None
    )

    return VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=is_btrfs,
        hardlink_supported=hardlink_supported,
        btrfs_user_subvol_rm=btrfs_user_subvol_rm,
        mount=mount_caps,
    )


def _sentinel_only_capabilities(
    volume: Volume,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
    *,
    run: VolumeCommandRunner,
) -> VolumeCapabilities:
    """Minimal capabilities for a reachable volume whose sentinel is missing.

    Only ``sentinel_exists`` is meaningful; the remaining fields are
    not probed and carry safe defaults.  Mount status is still probed
    when the volume has mount config — mount state is independent of
    the sentinel and represents a prerequisite for it (the drive must
    be mounted before the sentinel can exist).
    """
    mount_caps = (
        _check_mount_capabilities(
            volume,
            volume.mount,
            mount_tools,
            resolved_endpoints,
            mount_observation,
            run=run,
        )
        if volume.mount is not None
        else None
    )
    return VolumeCapabilities(
        sentinel_exists=False,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=mount_caps,
    )
