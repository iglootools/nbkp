"""Dry-run planning for the mount lifecycle.

``disks mount --dry-run`` / ``disks umount --dry-run`` report what *would*
happen without calling any mutating ``udisksctl`` command: the current state
is probed with the same read-only queries ``disks status`` uses, and the
lifecycle steps that state calls for are listed.
"""

import enum
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config, MountConfig, Volume
from ..remote.endpoints import ResolvedEndpoints
from ..remote.errors import describe_error, ssh_connection_errors
from .lifecycle import lifecycle_volumes
from .models import MountCapabilities
from .mount_checks import check_mount_status

type MountProbe = Callable[[Volume, MountConfig, ResolvedEndpoints], MountCapabilities]
"""Reads a volume's current mount state: :func:`check_mount_status` in
production, a fake in tests."""


class MountAction(str, enum.Enum):
    """A mutating lifecycle step."""

    UNLOCK = "unlock"
    MOUNT = "mount"
    UNMOUNT = "unmount"
    LOCK = "lock"


@dataclass(frozen=True)
class VolumePlan:
    volume_slug: str
    actions: tuple[MountAction, ...]
    detail: str | None = None
    """Why nothing (more) would be done, or why the state is unknown."""
    probe_failed: bool = False


def _mount_actions(
    mount: MountConfig, caps: MountCapabilities
) -> tuple[tuple[MountAction, ...], str | None]:
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
    probe: MountProbe,
) -> VolumePlan:
    try:
        caps = probe(volume, mount, resolved)
    except ssh_connection_errors() as e:
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
    probe: MountProbe = check_mount_status,
) -> list[VolumePlan]:
    """Plan mount (``mounting=True``) or umount steps for mount-managed volumes."""
    return [
        _plan_one(vol, mount, resolved, mounting=mounting, probe=probe)
        for _slug, vol, mount in lifecycle_volumes(
            config, resolved, names, mounting=mounting
        )
    ]


def plan_json(plans: list[VolumePlan]) -> list[dict[str, object]]:
    return [
        {
            "volume": plan.volume_slug,
            "actions": [action.value for action in plan.actions],
            "detail": plan.detail,
            "probe_failed": plan.probe_failed,
        }
        for plan in plans
    ]
