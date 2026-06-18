"""Mount state probing: tool availability, config validation, runtime state.

Probes whether udisks tools are installed and whether volumes are currently
unlocked/mounted.  Used by both ``disks status`` and preflight checks.
"""

from __future__ import annotations

from ..config import (
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from .detection import (
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_target_device,
)
from .models import MountCapabilities, MountToolCapabilities
from .observation import MountObservation
from ..remote.queries import _check_command_available
from ..remote.dispatch import run_on_volume

# udisks2 btrfs module locations (best-effort across distros).  The module
# (from the ``udisks2-btrfs`` package) is ``libudisks2_btrfs.so`` under
# ``udisks2/modules/`` — distinct from the libblockdev ``libbd_btrfs`` library.
_BTRFS_MODULE_PROBE = (
    "ls /usr/lib/*/udisks2/modules/libudisks2_btrfs.so "
    "/usr/lib/udisks2/modules/libudisks2_btrfs.so "
    "/usr/libexec/udisks2/modules/libudisks2_btrfs.so 2>/dev/null | grep -q ."
)


def probe_mount_tools(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> MountToolCapabilities:
    """Probe mount management tool availability on the host.

    Probes all tools that might be needed by any volume on this host.
    Which tools are actually *required* is determined during error
    interpretation (``SshEndpointToolNeeds``).
    """
    has_udisksctl = _check_command_available(volume, "udisksctl", resolved_endpoints)
    has_findmnt = _check_command_available(volume, "findmnt", resolved_endpoints)
    has_lsblk = _check_command_available(volume, "lsblk", resolved_endpoints)
    udisksd_running = (
        run_on_volume(["udisksctl", "status"], volume, resolved_endpoints).returncode
        == 0
        if has_udisksctl
        else False
    )
    has_btrfs_module = (
        run_on_volume(
            ["sh", "-c", _BTRFS_MODULE_PROBE], volume, resolved_endpoints
        ).returncode
        == 0
    )
    return MountToolCapabilities(
        has_udisksctl=has_udisksctl,
        udisksd_running=udisksd_running,
        has_btrfs_module=has_btrfs_module,
        has_findmnt=has_findmnt,
        has_lsblk=has_lsblk,
    )


def _check_fstab_entry(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str | None:
    """Return the fstab TARGET for *path*, or None when no fstab entry exists.

    Confirms udisks will mount the device at the declared *path* (rather than
    at ``/run/media/...``).  Only meaningful when ``volume.path`` is declared.
    """
    # udisks2 could answer this via the Block.Configuration property (the
    # device's tracked fstab/crypttab entries, ``udisksctl info -b <device>``),
    # but we use findmnt --fstab for symmetry with the other mount queries (see
    # detection.find_mountpoint). The query that rules out dropping findmnt
    # entirely — the live mount option string — has no udisks equivalent; see
    # preflight.snapshot_checks.check_btrfs_mount_option.
    result = run_on_volume(
        ["findmnt", "--fstab", "--target", path, "-n", "-o", "TARGET"],
        volume,
        resolved_endpoints,
    )
    if result.returncode != 0:
        return None
    target = result.stdout.strip().splitlines()
    return target[0].strip() if target and target[0].strip() else None


def check_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> MountCapabilities:
    """Probe volume-specific mount config and runtime state.

    Tool availability comes from *mount_tools* at the SSH endpoint level.
    This function probes the fstab entry (when ``volume.path`` is declared)
    and the runtime unlock/mount state.  When *mount_observation* is
    available, runtime state is reused instead of re-probing over SSH.
    """
    obs = mount_observation
    encrypted = mount.encryption is not None

    # fstab check only matters for the fixed-path (Option A) model.
    fstab_target = (
        _check_fstab_entry(volume, volume.path, resolved_endpoints)
        if volume.path is not None
        else None
    )
    has_fstab_entry = fstab_target is not None if volume.path is not None else None

    # Runtime state — reuse observation when available, else probe.
    if obs is not None:
        device_present = obs.device_present
        luks_unlocked = obs.luks_unlocked
        mounted = obs.mounted
        cleartext_device = obs.cleartext_device
        effective_path = obs.effective_path
        failure_reason = obs.mount_failure_reason
    else:
        device_present = detect_device_present(
            volume, mount.device_uuid, resolved_endpoints
        )
        cleartext_device = (
            discover_cleartext_device(volume, mount.device_uuid, resolved_endpoints)
            if encrypted and device_present
            else None
        )
        luks_unlocked = (cleartext_device is not None) if encrypted else None
        target_device = resolve_target_device(volume, mount, resolved_endpoints)
        effective_path = (
            find_mountpoint(volume, target_device, resolved_endpoints)
            if target_device is not None
            else None
        )
        mounted = effective_path is not None
        failure_reason = None

    return MountCapabilities(
        has_fstab_entry=has_fstab_entry,
        fstab_target=fstab_target,
        device_present=device_present,
        luks_unlocked=luks_unlocked,
        mounted=mounted,
        cleartext_device=cleartext_device,
        effective_path=effective_path,
        mount_failure_reason=failure_reason,
    )


def check_mount_status(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_tools: MountToolCapabilities | None = None,
) -> MountCapabilities:
    """Probe mount capabilities and runtime state for a single volume.

    Lightweight alternative to ``check_volume_capabilities`` — only probes
    mount-related capabilities (fstab + runtime device/luks/mounted state).

    When *mount_tools* is ``None``, probes mount tools on the fly.
    """
    re = resolved_endpoints or {}
    tools = mount_tools or probe_mount_tools(volume, re)
    return check_mount_capabilities(volume, mount, tools, re)
