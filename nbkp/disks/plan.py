"""
Dry-run planning for the mount lifecycle.

``disks mount --dry-run`` / ``disks umount --dry-run`` report what *would*
happen without calling any mutating ``udisksctl`` command: the current state
is probed with the same read-only queries ``disks status`` uses, and the
lifecycle steps that state calls for are listed.
"""

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from nbkp.config import Config, MountConfig, Volume
from nbkp.disks.lifecycle import lifecycle_volumes, missing_local_tool
from nbkp.disks.models import MountCapabilities
from nbkp.disks.mount_checks import check_mount_status
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import describe_error, ssh_connection_errors

type MountProbeFn = Callable[
    [Volume, MountConfig, ResolvedEndpoints],
    MountCapabilities,
]
"""Reads a volume's current mount state: :func:`check_mount_status` in
production, a fake in tests."""


class MountAction(enum.StrEnum):
    """A mutating lifecycle step."""

    UNLOCK = "unlock"
    MOUNT = "mount"
    UNMOUNT = "unmount"
    LOCK = "lock"


@dataclass(frozen=True)
class VolumePlan:
    """
    The lifecycle steps a dry run would take on one volume, in order.

    ``probe_failed`` is set when the volume's state could not be probed, so
    the plan cannot say what the real run would do.
    """

    volume_slug: str
    actions: tuple[MountAction, ...]
    detail: str | None = None
    """Why nothing (more) would be done, or why the state is unknown."""
    probe_failed: bool = False


def _mount_actions(
    mount: MountConfig,
    caps: MountCapabilities,
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
    mount: MountConfig,
    caps: MountCapabilities,
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
    probe: MountProbeFn,
) -> VolumePlan:
    try:
        caps = probe(volume, mount, resolved)
    except ssh_connection_errors() as e:
        return VolumePlan(
            volume.slug,
            (),
            probe_error_detail(volume, e),
            probe_failed=True,
        )
    if _state_unknown(mount, caps):
        return VolumePlan(volume.slug, (), "device state unknown", probe_failed=True)
    else:
        actions, detail = (
            _mount_actions(mount, caps) if mounting else _umount_actions(mount, caps)
        )
        return VolumePlan(volume.slug, actions, detail)


def probe_error_detail(volume: Volume, error: BaseException) -> str:
    """
    Return why a volume's state could not be probed.

    A local tool not installed, or the host unreachable.
    """
    match missing_local_tool(volume, error):
        case str() as tool:
            return f"{tool} not installed"
        case None:
            return f"unreachable: {describe_error(error)}"


def _state_unknown(mount: MountConfig, caps: MountCapabilities) -> bool:
    """
    Return whether a probe left the state unknown.

    A failed ``lsblk``/``findmnt`` probe leaves the state ``None`` rather than raising,
    and a plan built on it would read ``None`` as "not mounted".
    """
    return caps.device_present is not False and (
        caps.mounted is None
        or (mount.encryption is not None and caps.luks_unlocked is None)
    )


def plan_lifecycle(
    config: Config,
    resolved: ResolvedEndpoints,
    *,
    mounting: bool,
    names: list[str] | None = None,
    probe: MountProbeFn = check_mount_status,
) -> list[VolumePlan]:
    """Plan mount (``mounting=True``) or umount steps for mount-managed volumes."""
    return [
        _plan_one(vol, mount, resolved, mounting=mounting, probe=probe)
        for _slug, vol, mount in lifecycle_volumes(
            config,
            resolved,
            names,
            mounting=mounting,
        )
    ]


def plan_incomplete(plans: Sequence[VolumePlan]) -> bool:
    """
    Whether a volume's state could not be probed.

    The real run would fail on it too, so a dry run that cannot tell what it
    would do must not report success.
    """
    return any(plan.probe_failed for plan in plans)


def plan_json(plans: list[VolumePlan]) -> list[dict[str, object]]:
    """Return the plans as JSON-ready dicts for ``--output json``."""
    return [
        {
            "volume": plan.volume_slug,
            "actions": [action.value for action in plan.actions],
            "detail": plan.detail,
            "probe_failed": plan.probe_failed,
        }
        for plan in plans
    ]
