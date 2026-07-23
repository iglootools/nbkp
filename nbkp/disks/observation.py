"""Pre-computed mount state from mount lifecycle, reusable by preflight."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from .lifecycle import MountFailureReason, MountResult


@dataclass(frozen=True)
class MountObservation:
    """Pre-computed mount state from mount lifecycle, reusable by preflight.

    Fields mirror the runtime-state subset of ``MountCapabilities``.
    Preflight checks can use these values instead of re-probing device
    presence, LUKS unlock, and mount state via SSH.
    """

    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    cleartext_device: str | None = None
    effective_path: str | None = None
    failure_reason: MountFailureReason | None = None
    """Specific cause when the lifecycle step failed, for preflight to
    upgrade the generic VOLUME_NOT_MOUNTED to a more actionable error."""

    @property
    def mount_failure_reason(self) -> str | None:
        """Stringified ``failure_reason``.

        Mirrors ``MountCapabilities.mount_failure_reason`` so that the
        ``MountStatusData`` protocol can expose a single accessor for
        both pre-lifecycle and post-lifecycle status objects.
        """
        return self.failure_reason.value if self.failure_reason is not None else None


def build_mount_observations(
    mount_results: list[MountResult],
) -> dict[str, MountObservation]:
    """Build mount observations from lifecycle results for preflight reuse.

    The lifecycle already records the runtime state it observed on each
    ``MountResult`` (device present, LUKS unlocked, mounted, effective path),
    so this is a direct projection — no re-derivation from failure reasons.
    ``UNREACHABLE`` results yield no observation (nothing was learned).
    """
    observations: dict[str, MountObservation] = {}
    for result in mount_results:
        if result.failure_reason is MountFailureReason.UNREACHABLE:
            continue
        observations[result.volume_slug] = MountObservation(
            device_present=result.device_present,
            luks_unlocked=result.luks_unlocked,
            mounted=result.mounted,
            cleartext_device=result.cleartext_device,
            effective_path=result.effective_path,
            failure_reason=result.failure_reason,
        )
    return observations


def apply_effective_paths(
    config: Config,
    observations: dict[str, MountObservation],
) -> Config:
    """Return a copy of *config* with discovered mount paths filled in.

    For each mount-managed volume whose ``path`` was omitted (Option B,
    discovered mountpoint), substitute the effective path observed during the
    mount lifecycle so that downstream consumers (sentinels, rsync, snapshots,
    preflight) operate on a concrete location.  Volumes with a declared path,
    or whose device was not mounted, are left untouched.
    """
    updated = {}
    for slug, vol in config.volumes.items():
        obs = observations.get(slug)
        if (
            vol.mount is not None
            and vol.path is None
            and obs is not None
            and obs.effective_path is not None
        ):
            updated[slug] = vol.model_copy(update={"path": obs.effective_path})
    if not updated:
        return config
    return config.model_copy(update={"volumes": {**config.volumes, **updated}})
