"""Pre-computed mount state from mount lifecycle, reusable by preflight."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from .lifecycle import MountFailureReason, MountResult
from .strategy import (
    DirectMountStrategy,
    MountStrategy,
    SystemdMountStrategy,
)


@dataclass(frozen=True)
class MountObservation:
    """Pre-computed mount state from mount lifecycle, reusable by preflight.

    Fields mirror the runtime-state subset of ``MountCapabilities``.
    Preflight checks can use these values instead of re-probing device
    presence, LUKS attachment, and mount state via SSH.
    """

    resolved_backend: str
    mount_unit: str | None = None
    systemd_cryptsetup_path: str | None = None
    device_present: bool = False
    luks_attached: bool | None = None
    mounted: bool | None = None


def build_mount_observations(
    mount_results: list[MountResult],
    mount_strategy: dict[str, MountStrategy],
    config: Config,
) -> dict[str, MountObservation]:
    """Build mount observations from lifecycle results for preflight reuse.

    Maps each volume slug to a ``MountObservation`` by combining
    the resolved strategy (backend, mount unit, cryptsetup path) with
    the mount result (success/failure reason → runtime state).
    """
    results_by_slug = {r.volume_slug: r for r in mount_results}
    observations: dict[str, MountObservation] = {}

    for slug, strategy in mount_strategy.items():
        result = results_by_slug.get(slug)
        if result is None:
            continue

        # Extract backend info from strategy type
        match strategy:
            case SystemdMountStrategy():
                backend = "systemd"
                mount_unit = strategy.mount_unit
                cryptsetup_path = strategy.cryptsetup_path
            case DirectMountStrategy():
                backend = "direct"
                mount_unit = None
                cryptsetup_path = None
            case _:
                continue

        vol = config.volumes.get(slug)
        has_encryption = (
            vol is not None
            and vol.mount is not None
            and vol.disks.encryption is not None
        )

        # Infer runtime state from mount result
        match result.failure_reason:
            case None:
                # Success: everything is up
                observations[slug] = MountObservation(
                    resolved_backend=backend,
                    mount_unit=mount_unit,
                    systemd_cryptsetup_path=cryptsetup_path,
                    device_present=True,
                    luks_attached=True if has_encryption else None,
                    mounted=True,
                )
            case MountFailureReason.DEVICE_NOT_PRESENT:
                observations[slug] = MountObservation(
                    resolved_backend=backend,
                    mount_unit=mount_unit,
                    systemd_cryptsetup_path=cryptsetup_path,
                    device_present=False,
                    luks_attached=None,
                    mounted=None,
                )
            case MountFailureReason.ATTACH_LUKS_FAILED:
                observations[slug] = MountObservation(
                    resolved_backend=backend,
                    mount_unit=mount_unit,
                    systemd_cryptsetup_path=cryptsetup_path,
                    device_present=True,
                    luks_attached=False,
                    mounted=None,
                )
            case MountFailureReason.MOUNT_FAILED:
                observations[slug] = MountObservation(
                    resolved_backend=backend,
                    mount_unit=mount_unit,
                    systemd_cryptsetup_path=cryptsetup_path,
                    device_present=True,
                    luks_attached=True if has_encryption else None,
                    mounted=False,
                )
            case MountFailureReason.STRATEGY_NOT_RESOLVED:
                # No useful observation — skip
                pass
            case MountFailureReason.UNREACHABLE:
                # Host unreachable — no useful observation
                pass

    return observations
