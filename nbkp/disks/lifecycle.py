"""Mount/umount lifecycle orchestration via udisks2 (``udisksctl``)."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from ..config import (
    Config,
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..credentials import CredentialError
from ..remote.dispatch import run_on_volume
from ..remote.errors import SSH_CONNECTION_ERRORS, describe_error
from ..remote.fabricssh import StdinClosedProcess
from .detection import (
    DeviceProbeError,
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_target_device,
)
from .models import MountFailureReason
from .udisks import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_unmount_command,
    parse_unlocked_device,
)

# stderr fragments emitted by udisks/polkit when authorization is refused
# under ``--no-user-interaction`` (no active session, no polkit rule).
_NOT_AUTHORIZED_SIGNATURES = (
    "not authorized",
    "notauthorized",
)

# stderr fragments indicating udisksctl/udisksd is unavailable.
_UDISKS_UNAVAILABLE_SIGNATURES = (
    "command not found",
    "not found",
    "error connecting to the system message bus",
    "org.freedesktop.dbus.error.serviceunknown",
)

# Transient post-unlock races worth a brief retry: right after unlock, udisks
# may not have probed the cleartext filesystem yet (blkid/udev run
# asynchronously), so the mount momentarily reports the device as having no
# mountable filesystem, or can't look up its D-Bus object.  Also covers
# slow/loaded remote hosts where udev lags.
_TRANSIENT_MOUNT_SIGNATURES = (
    "is not a mountable filesystem",
    "looking up object",
    "no such interface",
)

_MOUNT_RETRY_ATTEMPTS = 8
_MOUNT_RETRY_DELAY_S = 0.5


def _mount_with_retry(
    device: str,
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
) -> subprocess.CompletedProcess[str]:
    """Run ``udisksctl mount`` for *device*, retrying transient post-unlock races."""
    result = run_on_volume(build_mount_command(device), volume, resolved_endpoints)
    attempts = 1
    while (
        result.returncode != 0
        and attempts < _MOUNT_RETRY_ATTEMPTS
        and any(sig in result.stderr.lower() for sig in _TRANSIENT_MOUNT_SIGNATURES)
    ):
        time.sleep(_MOUNT_RETRY_DELAY_S)
        result = run_on_volume(build_mount_command(device), volume, resolved_endpoints)
        attempts += 1
    return result


# Partition of MountFailureReason by lifecycle stage.  Consumed by
# ``nbkp.disks.output`` to disambiguate "real failure at this stage" (✗)
# from "no action attempted" (⚠).  ``NOT_AUTHORIZED`` appears in both
# because polkit can refuse at either the unlock or the mount step; the
# column that is actually ``False`` is the one that failed, so including it
# in both colours only the failed cell.
LUKS_STAGE_FAILURES: frozenset[MountFailureReason] = frozenset(
    {
        MountFailureReason.PASSPHRASE_NOT_AVAILABLE,
        MountFailureReason.UNLOCK_FAILED,
        MountFailureReason.NOT_AUTHORIZED,
    }
)


MOUNT_STAGE_FAILURES: frozenset[MountFailureReason] = frozenset(
    {
        MountFailureReason.MOUNT_FAILED,
        MountFailureReason.NOT_AUTHORIZED,
    }
)


def _classify_udisks_failure(
    result: subprocess.CompletedProcess[str],
    failed: MountFailureReason,
    action: str,
) -> tuple[MountFailureReason, str]:
    """Classify a failed udisksctl step into a reason + user-facing detail.

    A polkit refusal (``--no-user-interaction`` with no rule) maps to
    ``NOT_AUTHORIZED`` so preflight can route to the polkit-rules fix; a
    missing daemon/binary maps to ``UDISKS_NOT_AVAILABLE``; anything else is
    the stage-specific ``failed`` reason.
    """
    stderr = result.stderr.strip()
    lowered = stderr.lower()
    if isinstance(result, StdinClosedProcess) or any(
        sig in lowered for sig in _NOT_AUTHORIZED_SIGNATURES
    ):
        return (
            MountFailureReason.NOT_AUTHORIZED,
            f"{action} not authorized — polkit rule not configured",
        )
    elif any(sig in lowered for sig in _UDISKS_UNAVAILABLE_SIGNATURES):
        return (
            MountFailureReason.UDISKS_NOT_AVAILABLE,
            f"{action} failed — udisks2 not available: {stderr}",
        )
    else:
        return (failed, f"{action} failed (exit {result.returncode}): {stderr}")


@dataclass(frozen=True)
class MountResult:
    """Result of attempting to mount a single volume.

    Carries the runtime state observed during the lifecycle so that
    ``build_mount_observations`` can reuse it without re-probing, and so the
    effective mountpoint (declared or discovered) can be plumbed downstream.
    """

    volume_slug: str
    success: bool
    detail: str | None = None
    failure_reason: MountFailureReason | None = None
    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    cleartext_device: str | None = None
    effective_path: str | None = None


@dataclass(frozen=True)
class UmountResult:
    """Result of attempting to umount a single volume."""

    volume_slug: str
    success: bool
    detail: str | None = None
    warning: str | None = None


def _volumes_with_mount_config(
    config: Config,
    names: list[str] | None = None,
) -> list[tuple[str, Volume, MountConfig]]:
    """Return volumes that have mount config, optionally filtered by name."""
    return [
        (slug, vol, vol.mount)
        for slug, vol in config.volumes.items()
        if vol.mount is not None and (names is None or slug in names)
    ]


def unknown_volume_names(config: Config, names: list[str] | None) -> list[str]:
    """Names passed via ``--name`` that match no volume in *config*.

    Validated up front so that a typo fails loudly instead of silently
    matching nothing and reporting success.
    """
    return [name for name in names or [] if name not in config.volumes]


def mount_count(
    config: Config,
    names: list[str] | None = None,
) -> int:
    """Return the number of volumes with mount config, optionally filtered by name."""
    return len(_volumes_with_mount_config(config, names))


def mount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
) -> MountResult:
    """Mount a single volume via udisks. Idempotent: skips already mounted.

    Connection failures (timeout, DNS, SSH errors) yield a failed
    ``MountResult`` with ``UNREACHABLE`` rather than crashing, and a failed
    device probe yields ``PROBE_FAILED``.  Anything else — a programming
    error — propagates.
    """
    try:
        return _mount_volume_inner(
            volume, mount_config, resolved_endpoints, passphrase_fn
        )
    except DeviceProbeError as e:
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            detail=f"device state unknown: {e}",
            failure_reason=MountFailureReason.PROBE_FAILED,
            device_present=True,
        )
    except SSH_CONNECTION_ERRORS as e:
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            detail=f"unreachable: {describe_error(e)}",
            failure_reason=MountFailureReason.UNREACHABLE,
        )


def _mount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
) -> MountResult:
    """Core mount logic, separated for exception boundary in ``mount_volume``."""
    if not detect_device_present(volume, mount_config.device_uuid, resolved_endpoints):
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            detail=f"device not plugged in (UUID: {mount_config.device_uuid})",
            failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
            device_present=False,
        )
    match _resolve_device_to_mount(
        volume, mount_config, resolved_endpoints, passphrase_fn
    ):
        case MountResult() as failure:
            return failure
        case str() as device:
            return _mount_device(volume, mount_config, device, resolved_endpoints)


def _resolve_device_to_mount(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
) -> str | MountResult:
    """The block device to mount, unlocking the LUKS container first if needed.

    Returns a failed ``MountResult`` when the device cannot be produced.
    """
    match mount_config.encryption:
        case None:
            return f"/dev/disk/by-uuid/{mount_config.device_uuid}"
        case encryption:
            existing = discover_cleartext_device(
                volume, mount_config.device_uuid, resolved_endpoints
            )
            return existing or _unlock(
                volume,
                mount_config.device_uuid,
                encryption.passphrase_id,
                resolved_endpoints,
                passphrase_fn,
            )


def _unlock_failure(slug: str, reason: MountFailureReason, detail: str) -> MountResult:
    """Failed ``MountResult`` for the unlock stage (device is present)."""
    return MountResult(
        volume_slug=slug,
        success=False,
        detail=detail,
        failure_reason=reason,
        device_present=True,
        luks_unlocked=False,
    )


def _unlock(
    volume: Volume,
    device_uuid: str,
    passphrase_id: str,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
) -> str | MountResult:
    """Unlock a LUKS container and return its cleartext device.

    A passphrase that cannot be retrieved is reported as
    ``PASSPHRASE_NOT_AVAILABLE`` for a drive that *is* plugged in — the case
    prefetching deliberately leaves to this step (see
    :func:`credentials.prefetch_passphrases`).
    """
    try:
        passphrase = passphrase_fn(passphrase_id)
    except CredentialError as e:
        return _unlock_failure(
            volume.slug,
            MountFailureReason.PASSPHRASE_NOT_AVAILABLE,
            f"passphrase '{passphrase_id}' not available: {describe_error(e)}",
        )
    result = run_on_volume(
        build_unlock_command(device_uuid),
        volume,
        resolved_endpoints,
        input=passphrase,
    )
    if result.returncode != 0:
        reason, detail = _classify_udisks_failure(
            result, MountFailureReason.UNLOCK_FAILED, "unlock"
        )
        return _unlock_failure(volume.slug, reason, detail)
    # Prefer the device udisks reports on stdout ("Unlocked X as /dev/dm-N");
    # re-probing with lsblk here races the cleartext device's sysfs entry
    # appearing asynchronously after unlock.
    device = parse_unlocked_device(result.stdout) or discover_cleartext_device(
        volume, device_uuid, resolved_endpoints
    )
    return (
        device
        if device is not None
        else _unlock_failure(
            volume.slug,
            MountFailureReason.UNLOCK_FAILED,
            "unlocked device could not be resolved",
        )
    )


def _mount_device(
    volume: Volume,
    mount_config: MountConfig,
    device: str,
    resolved_endpoints: ResolvedEndpoints,
) -> MountResult:
    """Mount *device* unless it is already mounted."""
    encrypted = mount_config.encryption is not None
    result_for = partial(
        MountResult,
        volume_slug=volume.slug,
        device_present=True,
        luks_unlocked=True if encrypted else None,
        cleartext_device=device if encrypted else None,
    )
    existing = find_mountpoint(volume, device, resolved_endpoints)
    if existing is not None:
        return result_for(success=True, mounted=True, effective_path=existing)
    result = _mount_with_retry(device, volume, resolved_endpoints)
    if result.returncode != 0:
        reason, detail = _classify_udisks_failure(
            result, MountFailureReason.MOUNT_FAILED, "mount"
        )
        return result_for(
            success=False, detail=detail, failure_reason=reason, mounted=False
        )
    else:
        return result_for(
            success=True,
            mounted=True,
            effective_path=find_mountpoint(volume, device, resolved_endpoints),
        )


def umount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> UmountResult:
    """Umount and lock LUKS for a single volume.

    Always attempts umount + lock LUKS regardless of who mounted.
    Connection failures and failed device probes yield a failed
    ``UmountResult`` rather than crashing; programming errors propagate.
    """
    try:
        return _umount_volume_inner(volume, mount_config, resolved_endpoints)
    except DeviceProbeError as e:
        return UmountResult(
            volume_slug=volume.slug,
            success=False,
            detail=f"device state unknown: {e}",
            warning="volume may still be mounted or unlocked, check manually",
        )
    except SSH_CONNECTION_ERRORS as e:
        return UmountResult(
            volume_slug=volume.slug,
            success=False,
            detail=f"unreachable: {describe_error(e)}",
            warning="volume may still be mounted, manual umount needed",
        )


def _umount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> UmountResult:
    """Core umount logic, separated for exception boundary in ``umount_volume``."""
    slug = volume.slug

    device = resolve_target_device(volume, mount_config, resolved_endpoints)

    # 1. Umount if mounted
    if device is not None and find_mountpoint(volume, device, resolved_endpoints):
        result = run_on_volume(
            build_unmount_command(device), volume, resolved_endpoints
        )
        if result.returncode != 0:
            return UmountResult(
                volume_slug=slug,
                success=False,
                detail=(
                    f"umount failed (exit {result.returncode}): {result.stderr.strip()}"
                ),
                warning="device may still be mounted, manual umount needed",
            )

    # 2. Lock LUKS if encrypted and still unlocked
    if mount_config.encryption is not None and device is not None:
        result = run_on_volume(
            build_lock_command(mount_config.device_uuid),
            volume,
            resolved_endpoints,
        )
        if result.returncode != 0:
            return UmountResult(
                volume_slug=slug,
                success=True,
                warning=(
                    "umounted but lock-luks failed"
                    f" (exit {result.returncode}):"
                    f" {result.stderr.strip()}"
                ),
            )

    return UmountResult(volume_slug=slug, success=True)


def mount_volumes(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    names: list[str] | None = None,
    on_mount_start: Callable[[str], None] | None = None,
    on_mount_end: Callable[[str, MountResult], None] | None = None,
) -> list[MountResult]:
    """Mount all volumes with mount config. Idempotent: skips already mounted."""

    def _mount_one(slug: str, vol: Volume, mount_cfg: MountConfig) -> MountResult:
        if on_mount_start is not None:
            on_mount_start(slug)
        result = mount_volume(
            volume=vol,
            mount_config=mount_cfg,
            resolved_endpoints=resolved_endpoints,
            passphrase_fn=passphrase_fn,
        )
        if on_mount_end is not None:
            on_mount_end(slug, result)
        return result

    return [
        _mount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in _volumes_with_mount_config(config, names)
    ]


def umount_volumes(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    names: list[str] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
) -> list[UmountResult]:
    """Umount and lock LUKS for all volumes with mount config.

    Always umounts regardless of who mounted — avoids fragile
    action tracking across failed/restarted runs.  Umounts in reverse order.
    """

    def _umount_one(slug: str, vol: Volume, mount_cfg: MountConfig) -> UmountResult:
        if on_umount_start is not None:
            on_umount_start(slug)
        result = umount_volume(
            volume=vol,
            mount_config=mount_cfg,
            resolved_endpoints=resolved_endpoints,
        )
        if on_umount_end is not None:
            on_umount_end(slug, result)
        return result

    return [
        _umount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in reversed(_volumes_with_mount_config(config, names))
    ]
