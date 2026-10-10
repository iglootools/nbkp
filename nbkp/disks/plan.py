"""Dry-run planning for the mount lifecycle.

``disks mount --dry-run`` / ``disks umount --dry-run`` report what *would*
happen without calling any mutating ``udisksctl`` command: the current state
is probed with the same read-only queries ``disks status`` uses, and the
lifecycle steps that state calls for are listed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ..config import Config, MountConfig, Volume
from ..remote.endpoints import ResolvedEndpoints
from ..remote.errors import SSH_CONNECTION_ERRORS, describe_error
from .models import MountCapabilities
from .mount_checks import check_mount_status


class MountAction(str, enum.Enum):
    """A mutating lifecycle step."""

    UNLOCK = "unlock"
    MOUNT = "mount"
    UNMOUNT = "unmount"
    LOCK = "lock"


@dataclass(frozen=True)
class VolumePlan:
    """What the lifecycle would do for one volume."""

    volume_slug: str
    actions: tuple[MountAction, ...]
    detail: str | None = None
    """Why nothing (more) would be done, or why the state is unknown."""
    probe_failed: bool = False


def _mount_actions(
    mount: MountConfig, caps: MountCapabilities
) -> tuple[tuple[MountAction, ...], str | None]:
    """Mount-direction steps the probed state calls for."""
    match (caps.device_present, caps.mounted):
        case (False, _):
            return (), "device not plugged in"
        case (_, True):
            return (), f"already mounted at {caps.effective_path}"
        case _:
            return (
                *(
                    [MountAction.UNLOCK]
                    if mount.encryption is not None and caps.luks_unlocked is not True
                    else []
                ),
                MountAction.MOUNT,
            ), None


def _umount_actions(
    mount: MountConfig, caps: MountCapabilities
) -> tuple[tuple[MountAction, ...], str | None]:
    """Umount-direction steps the probed state calls for."""
    actions = (
        *([MountAction.UNMOUNT] if caps.mounted is True else []),
        *(
            [MountAction.LOCK]
            if mount.encryption is not None and caps.luks_unlocked is True
            else []
        ),
    )
    return actions, (None if actions else "not mounted")


def _plan_one(
    volume: Volume,
    mount: MountConfig,
    resolved: ResolvedEndpoints,
    *,
    mounting: bool,
) -> VolumePlan:
    try:
        caps = check_mount_status(volume, mount, resolved)
    except SSH_CONNECTION_ERRORS as e:
        return VolumePlan(
            volume.slug, (), f"unreachable: {describe_error(e)}", probe_failed=True
        )
    actions, detail = (
        _mount_actions(mount, caps) if mounting else _umount_actions(mount, caps)
    )
    return VolumePlan(volume.slug, actions, detail)


def plan_lifecycle(
    config: Config,
    resolved: ResolvedEndpoints,
    *,
    mounting: bool,
    names: list[str] | None = None,
) -> list[VolumePlan]:
    """Plan mount (``mounting=True``) or umount steps for mount-managed volumes.

    Umount is planned in reverse order, matching :func:`lifecycle.umount_volumes`.
    """
    volumes = [
        (vol, vol.mount)
        for slug, vol in config.volumes.items()
        if vol.mount is not None and (names is None or slug in names)
    ]
    ordered = volumes if mounting else list(reversed(volumes))
    return [
        _plan_one(vol, mount, resolved, mounting=mounting) for vol, mount in ordered
    ]


def plan_json(plans: list[VolumePlan]) -> list[dict[str, object]]:
    """JSON-serializable view of *plans*."""
    return [
        {
            "volume": plan.volume_slug,
            "actions": [action.value for action in plan.actions],
            "detail": plan.detail,
            "probe_failed": plan.probe_failed,
        }
        for plan in plans
    ]
