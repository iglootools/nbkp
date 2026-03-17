"""Mount/umount lifecycle orchestration."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable

from ..config import (
    Config,
    MountConfig,
    Volume,
)
from ..config.epresolution import ResolvedEndpoints
from ..remote.dispatch import run_on_volume
from .strategy import MountStrategy
from .detection import (
    StrategyResolutionError,
    _try_resolve_mount_strategy,
    detect_device_present,
    detect_luks_attached,
)


class MountFailureReason(str, enum.Enum):
    """Structured reason for a mount failure."""

    DEVICE_NOT_PRESENT = "device_not_present"
    ATTACH_LUKS_FAILED = "attach_luks_failed"
    MOUNT_FAILED = "mount_failed"
    STRATEGY_NOT_RESOLVED = "strategy_not_resolved"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class MountResult:
    """Result of attempting to mount a single volume."""

    volume_slug: str
    success: bool
    detail: str | None = None
    failure_reason: MountFailureReason | None = None


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


def mount_volume_count(
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
    mount_strategy: MountStrategy,
) -> MountResult:
    """Mount a single volume. Idempotent: skips already mounted.

    Catches connection failures (timeout, DNS, SSH errors) and returns
    a failed ``MountResult`` with ``UNREACHABLE`` rather than crashing.
    """
    slug = volume.slug

    try:
        return _mount_volume_inner(
            volume, mount_config, resolved_endpoints, passphrase_fn, mount_strategy
        )
    except Exception as e:
        return MountResult(
            volume_slug=slug,
            success=False,
            detail=f"unreachable: {e}",
            failure_reason=MountFailureReason.UNREACHABLE,
        )


def _mount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    mount_strategy: MountStrategy,
) -> MountResult:
    """Core mount logic, separated for exception boundary in ``mount_volume``."""
    slug = volume.slug

    # 1. Check device present
    if not detect_device_present(volume, mount_config.device_uuid, resolved_endpoints):
        return MountResult(
            volume_slug=slug,
            success=False,
            detail=f"device not plugged in (UUID: {mount_config.device_uuid})",
            failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
        )

    # 2. Attach LUKS if encrypted and not yet attached
    if mount_config.encryption is not None:
        enc = mount_config.encryption
        if not detect_luks_attached(volume, enc.mapper_name, resolved_endpoints):
            passphrase = passphrase_fn(enc.passphrase_id)
            cmd = mount_strategy.build_attach_luks_command(
                enc.mapper_name, mount_config.device_uuid
            )
            result = run_on_volume(cmd, volume, resolved_endpoints, input=passphrase)
            if result.returncode != 0:
                return MountResult(
                    volume_slug=slug,
                    success=False,
                    detail=(
                        f"attach-luks failed (exit {result.returncode}):"
                        f" {result.stderr.strip()}"
                    ),
                    failure_reason=MountFailureReason.ATTACH_LUKS_FAILED,
                )

    # 3. Mount if not already mounted
    if not mount_strategy.detect_mounted(volume, resolved_endpoints):
        cmd = mount_strategy.build_mount_command()
        result = run_on_volume(cmd, volume, resolved_endpoints)
        if result.returncode != 0:
            return MountResult(
                volume_slug=slug,
                success=False,
                detail=(
                    f"mount failed (exit {result.returncode}): {result.stderr.strip()}"
                ),
                failure_reason=MountFailureReason.MOUNT_FAILED,
            )

    return MountResult(volume_slug=slug, success=True)


def umount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    mount_strategy: MountStrategy,
) -> UmountResult:
    """Umount and close LUKS for a single volume.

    Always attempts umount + close LUKS regardless of who mounted.
    Catches connection failures and returns a failed ``UmountResult``
    rather than crashing.
    """
    slug = volume.slug

    try:
        return _umount_volume_inner(
            volume, mount_config, resolved_endpoints, mount_strategy
        )
    except Exception as e:
        return UmountResult(
            volume_slug=slug,
            success=False,
            detail=f"unreachable: {e}",
            warning="volume may still be mounted, manual umount needed",
        )


def _umount_volume_inner(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    mount_strategy: MountStrategy,
) -> UmountResult:
    """Core umount logic, separated for exception boundary in ``umount_volume``."""
    slug = volume.slug

    # 1. Umount
    if mount_strategy.detect_mounted(volume, resolved_endpoints):
        cmd = mount_strategy.build_umount_command()
        result = run_on_volume(cmd, volume, resolved_endpoints)
        if result.returncode != 0:
            return UmountResult(
                volume_slug=slug,
                success=False,
                detail=(
                    f"umount failed (exit {result.returncode}): {result.stderr.strip()}"
                ),
                warning="device may still be mounted, manual umount needed",
            )

    # 2. Close LUKS if encrypted and still attached
    if mount_config.encryption is not None:
        enc = mount_config.encryption
        if detect_luks_attached(volume, enc.mapper_name, resolved_endpoints):
            cmd = mount_strategy.build_close_luks_command(enc.mapper_name)
            result = run_on_volume(cmd, volume, resolved_endpoints)
            if result.returncode != 0:
                return UmountResult(
                    volume_slug=slug,
                    success=True,
                    warning=(
                        "umounted but close-luks failed"
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
    mount_strategy: dict[str, MountStrategy] | None = None,
    names: list[str] | None = None,
    on_mount_start: Callable[[str], None] | None = None,
    on_mount_end: Callable[[str, MountResult], None] | None = None,
) -> tuple[dict[str, MountStrategy], list[MountResult]]:
    """Mount all volumes with mount config. Idempotent: skips already mounted.

    When ``mount_strategy`` is ``None``, strategies are resolved
    per-volume inside the ``on_mount_start``/``on_mount_end`` window
    so that callers can show a spinner covering the entire lifecycle
    (strategy resolution + mount).

    Returns ``(resolved_strategies, results)`` — the strategy dict is
    needed by the umount phase and by observation building.
    """
    ms = dict(mount_strategy) if mount_strategy is not None else None

    def _mount_one(slug: str, vol: Volume, mount_cfg: MountConfig) -> MountResult:
        if on_mount_start is not None:
            on_mount_start(slug)

        # Resolve strategy inline when not pre-resolved
        if ms is not None:
            strategy = ms.get(slug)
            if strategy is None:
                result = MountResult(
                    volume_slug=slug,
                    success=False,
                    detail="mount strategy not resolved",
                    failure_reason=MountFailureReason.STRATEGY_NOT_RESOLVED,
                )
                if on_mount_end is not None:
                    on_mount_end(slug, result)
                return result
        else:
            outcome = _try_resolve_mount_strategy(vol, mount_cfg, resolved_endpoints)
            match outcome:
                case StrategyResolutionError() as error:
                    result = MountResult(
                        volume_slug=slug,
                        success=False,
                        detail=error.detail,
                        failure_reason=MountFailureReason.UNREACHABLE,
                    )
                    if on_mount_end is not None:
                        on_mount_end(slug, result)
                    return result
                case _:
                    strategy = outcome
                    resolved_strategies[slug] = strategy

        result = mount_volume(
            volume=vol,
            mount_config=mount_cfg,
            resolved_endpoints=resolved_endpoints,
            passphrase_fn=passphrase_fn,
            mount_strategy=strategy,
        )
        if on_mount_end is not None:
            on_mount_end(slug, result)
        return result

    resolved_strategies: dict[str, MountStrategy] = dict(ms) if ms is not None else {}
    results = [
        _mount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in _volumes_with_mount_config(config, names)
    ]
    return resolved_strategies, results


def umount_volumes(
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    mount_strategy: dict[str, MountStrategy] | None = None,
    names: list[str] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
) -> list[UmountResult]:
    """Umount and close LUKS for all volumes with mount config.

    Always umounts regardless of who mounted — avoids fragile
    action tracking across failed/restarted runs.
    Umounts in reverse order.

    When ``mount_strategy`` is ``None``, strategies are resolved
    per-volume inside the ``on_umount_start``/``on_umount_end`` window.
    """
    ms = dict(mount_strategy) if mount_strategy is not None else None

    def _umount_one(
        slug: str,
        vol: Volume,
        mount_cfg: MountConfig,
    ) -> UmountResult:
        if on_umount_start is not None:
            on_umount_start(slug)

        if ms is not None:
            strategy = ms.get(slug)
            if strategy is None:
                result = UmountResult(
                    volume_slug=slug,
                    success=False,
                    detail="mount strategy not resolved",
                )
                if on_umount_end is not None:
                    on_umount_end(slug, result)
                return result
        else:
            outcome = _try_resolve_mount_strategy(vol, mount_cfg, resolved_endpoints)
            match outcome:
                case StrategyResolutionError() as error:
                    result = UmountResult(
                        volume_slug=slug,
                        success=False,
                        detail=error.detail,
                    )
                    if on_umount_end is not None:
                        on_umount_end(slug, result)
                    return result
                case _:
                    strategy = outcome

        result = umount_volume(
            volume=vol,
            mount_config=mount_cfg,
            resolved_endpoints=resolved_endpoints,
            mount_strategy=strategy,
        )
        if on_umount_end is not None:
            on_umount_end(slug, result)
        return result

    return [
        _umount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in reversed(_volumes_with_mount_config(config, names))
    ]
