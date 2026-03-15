"""Mount/umount lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import (
    Config,
    MountConfig,
    ResolvedEndpoints,
    Volume,
)
from ..remote.dispatch import run_on_volume
from .strategy import MountStrategy
from .detection import (
    detect_device_present,
    detect_luks_attached,
)


@dataclass(frozen=True)
class MountResult:
    """Result of attempting to mount a single volume."""

    volume_slug: str
    success: bool
    detail: str | None = None


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


def mount_volume(
    volume: Volume,
    mount_config: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    mount_strategy: MountStrategy,
) -> MountResult:
    """Mount a single volume. Idempotent: skips already mounted."""
    slug = volume.slug

    # 1. Check device present
    if not detect_device_present(volume, mount_config.device_uuid, resolved_endpoints):
        return MountResult(
            volume_slug=slug,
            success=False,
            detail=f"device not plugged in (UUID: {mount_config.device_uuid})",
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
    """
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
) -> list[MountResult]:
    """Mount all volumes with mount config. Idempotent: skips already mounted."""
    ms = mount_strategy or {}

    def _mount_one(slug: str, vol: Volume, mount_cfg: MountConfig) -> MountResult:
        strategy = ms.get(slug)
        if strategy is None:
            return MountResult(
                volume_slug=slug,
                success=False,
                detail="mount strategy not resolved",
            )
        if on_mount_start is not None:
            on_mount_start(slug)
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

    return [
        _mount_one(slug, vol, mount_cfg)
        for slug, vol, mount_cfg in _volumes_with_mount_config(config, names)
    ]


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
    """
    ms = mount_strategy or {}

    def _umount_one(
        slug: str,
        vol: Volume,
        mount_cfg: MountConfig,
    ) -> UmountResult:
        strategy = ms.get(slug)
        if strategy is None:
            return UmountResult(
                volume_slug=slug,
                success=False,
                detail="mount strategy not resolved",
            )
        if on_umount_start is not None:
            on_umount_start(slug)
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
