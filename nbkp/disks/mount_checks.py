"""Mount state probing: tool availability, config validation, runtime state.

Probes whether udisks tools are installed and whether volumes are currently
unlocked/mounted.  Used by both ``disks status`` and preflight checks.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from ..config import (
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..remote.dispatch import run_on_volume
from ..remote.queries import _check_command_available
from .detection import (
    DeviceProbeError,
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
)
from .models import MountCapabilities, MountToolCapabilities
from .observation import MountObservation
from .udisks import cleartext_mapper_name

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


@dataclass(frozen=True)
class FstabEntry:
    """The ``/etc/fstab`` line whose mountpoint is the volume's declared path."""

    source: str
    """Device spec as written (``UUID=…``, ``/dev/mapper/…``, ``LABEL=…``)."""
    target: str


def _check_fstab_entry(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> FstabEntry | None:
    """Return the fstab entry whose TARGET is *path*, or None when there is none.

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
        ["findmnt", "--fstab", "--target", path, "-n", "-P", "-o", "SOURCE,TARGET"],
        volume,
        resolved_endpoints,
    )
    return (
        _parse_fstab_pairs(result.stdout.splitlines()[0])
        if result.returncode == 0 and result.stdout.strip()
        else None
    )


def _parse_fstab_pairs(line: str) -> FstabEntry | None:
    """Parse one ``findmnt -P`` line (``SOURCE="…" TARGET="…"``)."""
    pairs = dict(field.split("=", 1) for field in shlex.split(line) if "=" in field)
    return (
        FstabEntry(source=pairs["SOURCE"], target=pairs["TARGET"])
        if "SOURCE" in pairs and "TARGET" in pairs
        else None
    )


def fstab_source_matches(
    source: str,
    mount: MountConfig,
    cleartext_device: str | None,
) -> bool:
    """Whether an fstab SOURCE plausibly designates this volume's device.

    Only *contradictions* are rejected — a spec that names a different device
    in a form we can check.  Forms nbkp cannot resolve without extra probes
    (``LABEL=``, ``PARTUUID=``, ``/dev/sdX``, and for encrypted volumes the
    inner filesystem's ``UUID=``, which the config does not record) are given
    the benefit of the doubt: rejecting them would flag valid setups.

    - Unencrypted: ``UUID=<x>`` / ``/dev/disk/by-uuid/<x>`` must name
      ``device-uuid``.
    - Encrypted: the entry must not name the LUKS container itself (it
      cannot be mounted), and a ``/dev/mapper/<name>`` must be the cleartext
      device — the discovered one when unlocked, else udisks's default name.
    """
    uuid = mount.device_uuid.lower()
    spec = source.strip()
    lowered = spec.lower()
    names_uuid = lowered.removeprefix("uuid=").removeprefix("/dev/disk/by-uuid/")
    is_uuid_spec = names_uuid != lowered
    match mount.encryption:
        case None:
            return not is_uuid_spec or names_uuid == uuid
        case _:
            expected_mapper = cleartext_device or (
                f"/dev/mapper/{cleartext_mapper_name(uuid)}"
            )
            return not (is_uuid_spec and names_uuid == uuid) and (
                not spec.startswith("/dev/mapper/") or spec == expected_mapper
            )


@dataclass(frozen=True)
class _RuntimeState:
    device_present: bool | None
    luks_unlocked: bool | None
    mounted: bool | None
    cleartext_device: str | None
    effective_path: str | None


def _probe_runtime_state(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
) -> _RuntimeState:
    """Probe device presence, LUKS unlock and mount state over the wire.

    A tool reported missing by *mount_tools* is not invoked — the matching
    field stays ``None`` (unknown) and the SSH-endpoint layer reports the
    missing tool.  ``mount_tools=None`` means "not probed": try anyway.
    """
    encrypted = mount.encryption is not None
    can_lsblk = mount_tools is None or mount_tools.has_lsblk is not False
    can_findmnt = mount_tools is None or mount_tools.has_findmnt is not False
    device_present = detect_device_present(
        volume, mount.device_uuid, resolved_endpoints
    )
    cleartext_device, luks_known = (
        _probe_cleartext(volume, mount, resolved_endpoints)
        if encrypted and device_present and can_lsblk
        else (None, not encrypted or not device_present)
    )
    target_device = (
        cleartext_device if encrypted else f"/dev/disk/by-uuid/{mount.device_uuid}"
    )
    effective_path = (
        find_mountpoint(volume, target_device, resolved_endpoints)
        if target_device is not None and device_present and can_findmnt
        else None
    )
    mount_known = can_findmnt and luks_known
    return _RuntimeState(
        device_present=device_present,
        luks_unlocked=(cleartext_device is not None)
        if encrypted and luks_known
        else None,
        mounted=(effective_path is not None) if mount_known else None,
        cleartext_device=cleartext_device,
        effective_path=effective_path,
    )


def _probe_cleartext(
    volume: Volume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> tuple[str | None, bool]:
    """``(cleartext_device, known)`` — ``known`` is False when lsblk failed."""
    try:
        return (
            discover_cleartext_device(volume, mount.device_uuid, resolved_endpoints),
            True,
        )
    except DeviceProbeError:
        return None, False


def _observed_state(obs: MountObservation) -> _RuntimeState:
    return _RuntimeState(
        device_present=obs.device_present,
        luks_unlocked=obs.luks_unlocked,
        mounted=obs.mounted,
        cleartext_device=obs.cleartext_device,
        effective_path=obs.effective_path,
    )


def check_mount_capabilities(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
    mount_observation: MountObservation | None = None,
) -> MountCapabilities:
    """Probe volume-specific mount config and runtime state.

    Tool availability comes from *mount_tools* at the SSH endpoint level and
    gates which probes run.  This function probes the fstab entry (when
    ``volume.path`` is declared) and the runtime unlock/mount state.  When
    *mount_observation* is available, runtime state is reused instead of
    re-probing over SSH.
    """
    state = (
        _observed_state(mount_observation)
        if mount_observation is not None
        else _probe_runtime_state(volume, mount, mount_tools, resolved_endpoints)
    )
    fstab = _fstab_check(volume, mount, mount_tools, state, resolved_endpoints)
    return MountCapabilities(
        has_fstab_entry=fstab[0],
        fstab_target=fstab[1].target if fstab[1] is not None else None,
        fstab_source=fstab[1].source if fstab[1] is not None else None,
        device_present=state.device_present,
        luks_unlocked=state.luks_unlocked,
        mounted=state.mounted,
        cleartext_device=state.cleartext_device,
        effective_path=state.effective_path,
        mount_failure_reason=(
            mount_observation.failure_reason if mount_observation is not None else None
        ),
    )


def _fstab_check(
    volume: Volume,
    mount: MountConfig,
    mount_tools: MountToolCapabilities | None,
    state: _RuntimeState,
    resolved_endpoints: ResolvedEndpoints,
) -> tuple[bool | None, FstabEntry | None]:
    """``(has_matching_entry, entry)`` for the fixed-path (Option A) model.

    ``None`` when not applicable (no declared path) or not checkable (findmnt
    missing — reported at the SSH endpoint level instead of as a false
    mismatch).  An entry whose SOURCE names another device counts as no
    matching entry: udisks would not mount *this* device there.
    """
    if volume.path is None or (mount_tools is not None and not mount_tools.has_findmnt):
        return None, None
    else:
        entry = _check_fstab_entry(volume, volume.path, resolved_endpoints)
        return (
            entry is not None
            and fstab_source_matches(entry.source, mount, state.cleartext_device),
            entry,
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
    Tools are not probed here: with *mount_tools* ``None`` every probe is
    attempted and a missing tool surfaces as an unknown state.
    """
    return check_mount_capabilities(
        volume, mount, mount_tools, resolved_endpoints or {}
    )
