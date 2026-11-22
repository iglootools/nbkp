"""Mount/umount lifecycle orchestration via udisks2 (``udisksctl``)."""

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from nbkp.config import Config, LocalVolume, MountConfig, Volume
from nbkp.credentials import CredentialError
from nbkp.disks.detection import (
    DeviceProbeError,
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_target_device,
    settle_udev,
)
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountFailureKind,
    UmountFailureKind,
)
from nbkp.disks.udisks import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_unmount_command,
    parse_unlocked_device,
)
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints, location_excluded
from nbkp.remote.errors import describe_error, ssh_connection_errors

# stderr fragments emitted by udisks/polkit when authorization is refused
# under ``--no-user-interaction`` (no active session, no polkit rule).
_NOT_AUTHORIZED_SIGNATURES = (
    "not authorized",
    "notauthorized",
)

# Specific to a missing binary or daemon: udisks's own errors also say "not
# found" (a missing object or device), and must not send the operator off to
# install udisks.
_UDISKS_UNAVAILABLE_SIGNATURES = (
    "command not found",  # bash: udisksctl: command not found
    "udisksctl: not found",  # dash (sh -c): udisksctl: not found
    # udisksctl 2.10 with no system bus to reach udisksd through prints
    # Error connecting to the udisks daemon: Could not connect: Connection refused
    "error connecting to the udisks daemon",
    "error connecting to the system message bus",
    "org.freedesktop.dbus.error.serviceunknown",
)

# Transient udisks races worth a brief retry. udisks learns about block devices
# from udev events, asynchronously: right after a lock, an unlock or a drive being
# plugged in it may not have an object for the device yet ("looking up object"), or
# not have probed it ("no such interface", "is not a mountable filesystem"). Also
# covers slow or loaded remote hosts where udev lags. A wrong passphrase or a
# refused authorization matches none of these, so it is reported at once.
_TRANSIENT_UDISKS_SIGNATURES = (
    "is not a mountable filesystem",
    "looking up object",
    "no such interface",
)

_UDISKS_RETRY_ATTEMPTS = 8
_UDISKS_RETRY_DELAY_S = 0.5


def run_udisks(
    command: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    stdin: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    run: VolumeCommandRunner = run_on_volume,
) -> subprocess.CompletedProcess[str]:
    """Run a ``udisksctl`` command, retrying while udisks catches up with udev."""
    result = run(command, volume, resolved_endpoints, stdin=stdin)
    attempts = 1
    while (
        result.returncode != 0
        and attempts < _UDISKS_RETRY_ATTEMPTS
        and any(sig in result.stderr.lower() for sig in _TRANSIENT_UDISKS_SIGNATURES)
    ):
        settle_udev(volume, resolved_endpoints, run=run)
        sleep(_UDISKS_RETRY_DELAY_S)
        result = run(command, volume, resolved_endpoints, stdin=stdin)
        attempts += 1
    return result


# Partition of MountFailureKind by lifecycle stage.  Consumed by
# ``nbkp.disks.output`` to disambiguate "real failure at this stage" (✗)
# from "no action attempted" (⚠).  ``NOT_AUTHORIZED`` appears in both
# because polkit can refuse at either the unlock or the mount step; the
# column that is actually ``False`` is the one that failed, so including it
# in both colours only the failed cell.
LUKS_STAGE_FAILURES: frozenset[MountFailureKind] = frozenset(
    {
        MountFailureKind.PASSPHRASE_NOT_AVAILABLE,
        MountFailureKind.UNLOCK_FAILED,
        MountFailureKind.NOT_AUTHORIZED,
    },
)


MOUNT_STAGE_FAILURES: frozenset[MountFailureKind] = frozenset(
    {
        MountFailureKind.MOUNT_FAILED,
        MountFailureKind.NOT_AUTHORIZED,
    },
)


def _classify_udisks_failure(
    result: subprocess.CompletedProcess[str],
    failed: MountFailureKind,
    step: LifecycleStep,
) -> tuple[MountFailureKind, LifecycleFailure]:
    """
    Classify a failed udisksctl step into a reason and its facts.

    A polkit refusal (``--no-user-interaction`` with no rule) maps to
    ``NOT_AUTHORIZED`` so preflight can route to the polkit-rules fix; a
    missing daemon/binary maps to ``UDISKS_NOT_AVAILABLE``; anything else is
    the stage-specific ``failed`` reason.
    """
    failure = _command_failure(step, result)
    lowered = (failure.message or "").lower()
    if any(sig in lowered for sig in _NOT_AUTHORIZED_SIGNATURES):
        return MountFailureKind.NOT_AUTHORIZED, failure
    elif any(sig in lowered for sig in _UDISKS_UNAVAILABLE_SIGNATURES):
        return MountFailureKind.UDISKS_NOT_AVAILABLE, failure
    else:
        return failed, failure


def _command_failure(
    step: LifecycleStep,
    result: subprocess.CompletedProcess[str],
) -> LifecycleFailure:
    return LifecycleFailure(
        step=step,
        exit_code=result.returncode,
        message=result.stderr.strip(),
    )


def _probe_failure(error: DeviceProbeError) -> LifecycleFailure:
    return LifecycleFailure(
        command=error.command,
        exit_code=error.returncode,
        message=error.stderr,
    )


def missing_local_tool(volume: Volume, error: BaseException) -> str | None:
    """
    Return the tool an error caught as a connection failure says is not installed.

    Locally, a ``FileNotFoundError`` means ``udisksctl``, ``lsblk`` or
    ``findmnt`` is not installed; it is only a connection failure for a
    remote volume.
    """
    match volume, error:
        case LocalVolume(), FileNotFoundError(filename=str() as tool):
            return tool
        case _:
            return None


def _connection_failure(
    volume: Volume,
    error: BaseException,
) -> tuple[bool, LifecycleFailure]:
    """``(tool_missing, failure)`` for an error caught as a connection failure."""
    match missing_local_tool(volume, error):
        case str() as tool:
            return True, LifecycleFailure(command=tool)
        case None:
            return False, LifecycleFailure(message=describe_error(error))


@dataclass(frozen=True)
class MountResult:
    """
    Result of attempting to mount a single volume.

    Carries the runtime state observed during the lifecycle so that
    ``build_mount_observations`` can reuse it without re-probing, and so the
    effective mountpoint (declared or discovered) can be plumbed downstream.
    """

    volume_slug: str
    success: bool
    failure_reason: MountFailureKind | None = None
    failure: LifecycleFailure | None = None
    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    cleartext_device: str | None = None
    effective_path: str | None = None


@dataclass(frozen=True)
class UmountResult:
    """Result of attempting to unmount (and lock) a single volume."""

    volume_slug: str
    success: bool
    failure_reason: UmountFailureKind | None = None
    failure: LifecycleFailure | None = None


def any_failed(results: Sequence[MountResult | UmountResult]) -> bool:
    """
    Return whether a mount or umount pass left a volume short of its target state.

    Such a pass fails the command that ran it.
    """
    return any(not r.success for r in results)


def refused_by_polkit(results: Sequence[MountResult]) -> bool:
    """
    Return whether udisks refused an unlock or mount for want of a polkit rule.

    That is the failure ``disks setup-auth`` fixes.
    """
    return any(r.failure_reason is MountFailureKind.NOT_AUTHORIZED for r in results)


def lifecycle_volumes(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    names: list[str] | None = None,
    *,
    mounting: bool = True,
) -> list[tuple[str, Volume, MountConfig]]:
    """
    Return the mount-managed volumes a mount (or umount) acts on, in that order.

    Umount walks the mount order backwards. The one selection shared by the
    lifecycle, its dry-run plan and ``disks status``, so they cannot drift.
    A remote volume excluded by location has no endpoint to reach it through,
    so it is left out, as endpoint resolution promises.
    """
    volumes = [
        (slug, vol, vol.mount)
        for slug, vol in config.volumes.items()
        if vol.mount is not None
        and (names is None or slug in names)
        and not location_excluded(vol, resolved_endpoints)
    ]
    return volumes if mounting else list(reversed(volumes))


def unknown_volume_names(config: Config, names: list[str] | None) -> list[str]:
    """
    Names passed via ``--name`` that match no volume in *config*.

    Validated up front so that a typo fails loudly instead of silently
    matching nothing and reporting success.
    """
    return [name for name in names or [] if name not in config.volumes]


def mount_count(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    names: list[str] | None = None,
) -> int:
    """Return how many volumes a mount acts on, for progress display."""
    return len(lifecycle_volumes(config, resolved_endpoints, names))


def mount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> MountResult:
    """
    Mount a single volume via udisks. Idempotent: skips already mounted.

    Connection failures (timeout, DNS, SSH errors) yield a failed
    ``MountResult`` with ``UNREACHABLE`` rather than crashing, and a failed
    device probe yields ``PROBE_FAILED``.  Anything else — a programming
    error — propagates.
    """
    try:
        return _mount_volume_inner(
            volume,
            mount_config,
            resolved_endpoints,
            passphrase_fn,
            run=run,
        )
    except DeviceProbeError as e:
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            failure_reason=MountFailureKind.PROBE_FAILED,
            failure=_probe_failure(e),
            device_present=True,
        )
    except ssh_connection_errors() as e:
        tool_missing, failure = _connection_failure(volume, e)
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            failure_reason=(
                MountFailureKind.UDISKS_NOT_AVAILABLE
                if tool_missing
                else MountFailureKind.UNREACHABLE
            ),
            failure=failure,
        )


def _mount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    run: VolumeCommandRunner,
) -> MountResult:
    """Core mount logic, separated for exception boundary in ``mount_volume``."""
    settle_udev(volume, resolved_endpoints, run=run)
    if not detect_device_present(
        volume,
        mount_config.device_uuid,
        resolved_endpoints,
        run=run,
    ):
        return MountResult(
            volume_slug=volume.slug,
            success=False,
            failure_reason=MountFailureKind.DEVICE_NOT_PRESENT,
            failure=LifecycleFailure(device_uuid=mount_config.device_uuid),
            device_present=False,
        )
    match _resolve_device_to_mount(
        volume,
        mount_config,
        resolved_endpoints,
        passphrase_fn,
        run=run,
    ):
        case MountResult() as failure:
            return failure
        case str() as device:
            return _mount_device(
                volume,
                mount_config,
                device,
                resolved_endpoints,
                run=run,
            )


def _resolve_device_to_mount(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    run: VolumeCommandRunner,
) -> str | MountResult:
    """
    Return the block device to mount, unlocking the LUKS container first if needed.

    Returns a failed ``MountResult`` when the device cannot be produced.
    """
    match mount_config.encryption:
        case None:
            return f"/dev/disk/by-uuid/{mount_config.device_uuid}"
        case encryption:
            existing = discover_cleartext_device(
                volume,
                mount_config.device_uuid,
                resolved_endpoints,
                run=run,
            )
            return existing or _unlock(
                volume,
                mount_config.device_uuid,
                encryption.passphrase_id,
                resolved_endpoints,
                passphrase_fn,
                run=run,
            )


def _unlock_failure(
    slug: str,
    reason: MountFailureKind,
    failure: LifecycleFailure,
) -> MountResult:
    """Failed ``MountResult`` for the unlock stage (device is present)."""
    return MountResult(
        volume_slug=slug,
        success=False,
        failure_reason=reason,
        failure=failure,
        device_present=True,
        luks_unlocked=False,
    )


def _unlock(
    volume: Volume,
    device_uuid: str,
    passphrase_id: str,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    run: VolumeCommandRunner,
) -> str | MountResult:
    """
    Unlock a LUKS container and return its cleartext device.

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
            MountFailureKind.PASSPHRASE_NOT_AVAILABLE,
            LifecycleFailure(passphrase_id=passphrase_id, message=describe_error(e)),
        )
    result = run_udisks(
        build_unlock_command(device_uuid),
        volume,
        resolved_endpoints,
        stdin=passphrase,
        run=run,
    )
    if result.returncode != 0:
        reason, failure = _classify_udisks_failure(
            result,
            MountFailureKind.UNLOCK_FAILED,
            LifecycleStep.UNLOCK,
        )
        return _unlock_failure(volume.slug, reason, failure)
    # Prefer the device udisks reports on stdout ("Unlocked X as /dev/dm-N");
    # re-probing with lsblk here races the cleartext device's sysfs entry
    # appearing asynchronously after unlock.
    device = parse_unlocked_device(result.stdout) or discover_cleartext_device(
        volume,
        device_uuid,
        resolved_endpoints,
        run=run,
    )
    return (
        device
        if device is not None
        else _unlock_failure(
            volume.slug,
            MountFailureKind.UNLOCK_FAILED,
            # Unlocked, but no cleartext device found: no command failed.
            LifecycleFailure(step=LifecycleStep.UNLOCK),
        )
    )


def _mount_device(
    volume: Volume,
    mount_config: MountConfig,
    device: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> MountResult:
    encrypted = mount_config.encryption is not None
    result_for = partial(
        MountResult,
        volume_slug=volume.slug,
        device_present=True,
        luks_unlocked=True if encrypted else None,
        cleartext_device=device if encrypted else None,
    )
    existing = find_mountpoint(volume, device, resolved_endpoints, run=run)
    if existing is not None:
        return result_for(success=True, mounted=True, effective_path=existing)
    result = run_udisks(
        build_mount_command(device),
        volume,
        resolved_endpoints,
        run=run,
    )
    if result.returncode != 0:
        reason, failure = _classify_udisks_failure(
            result,
            MountFailureKind.MOUNT_FAILED,
            LifecycleStep.MOUNT,
        )
        return result_for(
            success=False,
            failure_reason=reason,
            failure=failure,
            mounted=False,
        )
    else:
        return result_for(
            success=True,
            mounted=True,
            effective_path=find_mountpoint(volume, device, resolved_endpoints, run=run),
        )


def umount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> UmountResult:
    """
    Umount and lock LUKS for a single volume.

    Always attempts umount + lock LUKS regardless of who mounted.
    Connection failures and failed device probes yield a failed
    ``UmountResult`` rather than crashing; programming errors propagate.
    """
    try:
        return _umount_volume_inner(volume, mount_config, resolved_endpoints, run=run)
    except DeviceProbeError as e:
        return UmountResult(
            volume_slug=volume.slug,
            success=False,
            failure_reason=UmountFailureKind.PROBE_FAILED,
            failure=_probe_failure(e),
        )
    except ssh_connection_errors() as e:
        tool_missing, failure = _connection_failure(volume, e)
        return UmountResult(
            volume_slug=volume.slug,
            success=False,
            failure_reason=(
                UmountFailureKind.UDISKS_NOT_AVAILABLE
                if tool_missing
                else UmountFailureKind.UNREACHABLE
            ),
            failure=failure,
        )


def _umount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> UmountResult:
    """Core umount logic, separated for exception boundary in ``umount_volume``."""
    slug = volume.slug

    device = resolve_target_device(volume, mount_config, resolved_endpoints, run=run)

    if device is not None and find_mountpoint(
        volume,
        device,
        resolved_endpoints,
        run=run,
    ):
        result = run_udisks(
            build_unmount_command(device),
            volume,
            resolved_endpoints,
            run=run,
        )
        if result.returncode != 0:
            return UmountResult(
                volume_slug=slug,
                success=False,
                failure_reason=UmountFailureKind.UNMOUNT_FAILED,
                failure=_command_failure(LifecycleStep.UNMOUNT, result),
            )

    # For an encrypted volume, a resolved device is the cleartext mapper, which
    # only exists while the container is unlocked.
    if mount_config.encryption is not None and device is not None:
        result = run_udisks(
            build_lock_command(mount_config.device_uuid),
            volume,
            resolved_endpoints,
            run=run,
        )
        if result.returncode != 0:
            # A failure, not a warning: the volume is left unlocked, which is
            # what the lifecycle exists to prevent.
            return UmountResult(
                volume_slug=slug,
                success=False,
                failure_reason=UmountFailureKind.LOCK_FAILED,
                failure=_command_failure(LifecycleStep.LOCK, result),
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
    run: VolumeCommandRunner = run_on_volume,
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
            run=run,
        )
        if on_mount_end is not None:
            on_mount_end(slug, result)
        return result

    return [
        _mount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in lifecycle_volumes(config, resolved_endpoints, names)
    ]


def umount_volumes(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    names: list[str] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
    run: VolumeCommandRunner = run_on_volume,
) -> list[UmountResult]:
    """
    Umount and lock LUKS for all volumes with mount config.

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
            run=run,
        )
        if on_umount_end is not None:
            on_umount_end(slug, result)
        return result

    return [
        _umount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in lifecycle_volumes(
            config,
            resolved_endpoints,
            names,
            mounting=False,
        )
    ]
