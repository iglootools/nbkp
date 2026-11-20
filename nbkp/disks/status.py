"""The mount status of each volume, as ``disks status`` and ``disks mount``
report it: probed, or why it was not."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..config import Config, Volume
from ..remote.dispatch import VolumeCommandRunner
from ..remote.endpoints import ResolvedEndpoints, location_excluded
from ..remote.errors import describe_error, ssh_connection_errors
from .lifecycle import MountResult, lifecycle_volumes, missing_local_tool
from .models import MountFailureKind
from .mount_checks import check_mount_status
from .observation import MountObservation, build_mount_observations


class MountStatusData(Protocol):
    """Structural protocol for mount runtime state.

    Satisfied by both ``MountObservation`` (dataclass) and
    ``MountCapabilities`` (Pydantic model).
    """

    @property
    def device_present(self) -> bool | None: ...

    @property
    def luks_unlocked(self) -> bool | None: ...

    @property
    def mounted(self) -> bool | None: ...

    @property
    def mount_failure_reason(self) -> MountFailureKind | None: ...


class VolumeStatusKind(StrEnum):
    PROBED = "probed"
    UNREACHABLE = "unreachable"
    TOOL_NOT_INSTALLED = "tool-not-installed"
    NOT_MANAGED = "not-managed"
    LOCATION_EXCLUDED = "location-excluded"


_UNKNOWN_STATE = MountObservation()


@dataclass(frozen=True)
class VolumeStatus:
    volume_slug: str
    kind: VolumeStatusKind
    state: MountStatusData = _UNKNOWN_STATE
    """The observed state; all unknown unless ``kind`` is ``PROBED``."""
    error_message: str | None = None
    """Why the host could not be reached (``UNREACHABLE``)."""
    missing_tool: str | None = None
    """The local tool not installed (``TOOL_NOT_INSTALLED``)."""


type ProbeStartFn = Callable[[Volume], None]
type ProbeEndFn = Callable[[Volume, VolumeStatus], None]


def _probe_failure_status(volume: Volume, error: BaseException) -> VolumeStatus:
    match missing_local_tool(volume, error):
        case str() as tool:
            return VolumeStatus(
                volume.slug, VolumeStatusKind.TOOL_NOT_INSTALLED, missing_tool=tool
            )
        case None:
            return VolumeStatus(
                volume.slug,
                VolumeStatusKind.UNREACHABLE,
                error_message=describe_error(error),
            )


def probe_volume_status(
    volume: Volume, resolved: ResolvedEndpoints, *, run: VolumeCommandRunner
) -> VolumeStatus:
    """Probe one mount-managed volume; a host that cannot be reached, or a
    local tool not installed, is a status rather than an exception."""
    assert volume.mount is not None
    try:
        state = check_mount_status(volume, volume.mount, resolved, run=run)
        return VolumeStatus(volume.slug, VolumeStatusKind.PROBED, state)
    except ssh_connection_errors() as e:
        return _probe_failure_status(volume, e)


def managed_volumes(
    cfg: Config, resolved: ResolvedEndpoints, names: list[str] | None = None
) -> list[Volume]:
    """The volumes ``probe_volume_statuses`` probes, in that order."""
    return [vol for _slug, vol, _mount in lifecycle_volumes(cfg, resolved, names)]


def probe_volume_statuses(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None = None,
    *,
    run: VolumeCommandRunner,
    on_probe_start: ProbeStartFn | None = None,
    on_probe_end: ProbeEndFn | None = None,
) -> list[VolumeStatus]:
    """Probe the mount-managed volumes, then list the others unprobed."""
    return [
        *(
            _probe_with_callbacks(vol, resolved, run, on_probe_start, on_probe_end)
            for vol in managed_volumes(cfg, resolved, names)
        ),
        *unprobed_volume_statuses(cfg, resolved, names),
    ]


def _probe_with_callbacks(
    volume: Volume,
    resolved: ResolvedEndpoints,
    run: VolumeCommandRunner,
    on_probe_start: ProbeStartFn | None,
    on_probe_end: ProbeEndFn | None,
) -> VolumeStatus:
    if on_probe_start is not None:
        on_probe_start(volume)
    status = probe_volume_status(volume, resolved, run=run)
    if on_probe_end is not None:
        on_probe_end(volume, status)
    return status


def _unprobed_kind(
    volume: Volume, resolved: ResolvedEndpoints
) -> VolumeStatusKind | None:
    match volume.mount:
        case None:
            return VolumeStatusKind.NOT_MANAGED
        case _ if location_excluded(volume, resolved):
            return VolumeStatusKind.LOCATION_EXCLUDED
        case _:
            return None


def unprobed_volume_statuses(
    cfg: Config, resolved: ResolvedEndpoints, names: list[str] | None = None
) -> list[VolumeStatus]:
    """Volumes listed without a probe: unmanaged, or excluded by location."""
    kinds = {
        slug: _unprobed_kind(vol, resolved)
        for slug, vol in cfg.volumes.items()
        if names is None or slug in names
    }
    return [
        VolumeStatus(slug, kind) for slug, kind in kinds.items() if kind is not None
    ]


def mount_result_statuses(results: list[MountResult]) -> list[VolumeStatus]:
    """The status of each volume ``disks mount`` acted on: the state the
    lifecycle observed, or why it could not reach the volume."""
    observations = build_mount_observations(results)
    return [
        VolumeStatus(
            r.volume_slug, VolumeStatusKind.PROBED, observations[r.volume_slug]
        )
        if r.volume_slug in observations
        else VolumeStatus(
            r.volume_slug,
            VolumeStatusKind.UNREACHABLE,
            error_message=r.failure.message if r.failure is not None else None,
        )
        for r in results
    ]
