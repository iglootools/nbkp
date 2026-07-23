"""Mount status display helpers (Rich tables and JSON)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence

from rich.table import Table

from ..clihelpers import Severity, Strictness, classify_severity, severity_icon
from .lifecycle import LUKS_STAGE_FAILURES, MOUNT_STAGE_FAILURES

if TYPE_CHECKING:
    from ..config.protocol.volume import LocalVolume, RemoteVolume


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
    def mount_failure_reason(self) -> str | None: ...


# ── Mount-state column severity ─────────────────────────────────
#
# These helpers are the single source of truth for severity in mount-state
# columns (``device``, ``LUKS``, ``mounted``) across both the disks status
# table and the preflight check display.  Each answers the question "did
# the action for this column actually attempt and fail?" using
# ``mount_failure_reason``, then routes the answer through
# ``classify_severity`` so strictness is respected.
#
# Note: the "is this a real failure?" rule lives here, separate from the
# preflight ``INACTIVE_*_ERRORS`` frozensets, because the mount-state
# table answers a different question than general preflight error
# classification (action-was-attempted vs. policy-inactive).


def device_fail_severity(
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``device_present=False``.

    A missing device is by definition an "observation, not failure"
    state, so it's always inactive at this layer and the strictness
    policy decides the final icon.
    """
    return classify_severity(is_inactive=True, strictness=strictness)


def luks_fail_severity(
    mount_failure_reason: str | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``luks_unlocked=False``.

    Real LUKS-stage failures (``UNLOCK_FAILED`` / ``NOT_AUTHORIZED``)
    are non-inactive — they're real errors and surface as ✗ under any
    strictness that doesn't ignore everything.  Other states (probe
    found the cleartext device missing but no unlock was attempted,
    or a cascading failure like ``DEVICE_NOT_PRESENT``) are inactive.
    """
    is_real_failure = mount_failure_reason in LUKS_STAGE_FAILURES
    return classify_severity(is_inactive=not is_real_failure, strictness=strictness)


def mounted_fail_severity(
    mount_failure_reason: str | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``mounted=False``.

    Real mount-stage failures (``MOUNT_FAILED`` / ``POLKIT_REFUSED``)
    are non-inactive.  Other states (probe-only, or a cascade from an
    earlier step) are inactive.
    """
    is_real_failure = mount_failure_reason in MOUNT_STAGE_FAILURES
    return classify_severity(is_inactive=not is_real_failure, strictness=strictness)


def display_name(vol: LocalVolume | RemoteVolume) -> str:
    """Display name for a volume: ``ssh-endpoint:slug`` for remote, ``slug`` for local."""
    from ..config.protocol.volume import RemoteVolume

    return (
        f"{vol.ssh_endpoint}:{vol.slug}" if isinstance(vol, RemoteVolume) else vol.slug
    )


def mount_state_icon(
    value: bool | None,
    *,
    fail_severity: Severity = Severity.ERROR,
) -> str:
    """Format a mount state value as checkmark, cross/warning, or dash.

    ``fail_severity`` lets callers downgrade a ``False`` value to a
    warning (orange) when the observation is non-fatal (e.g. a drive
    not being plugged in is observation noise, not a real failure).
    """
    match value:
        case True:
            return severity_icon(Severity.OK)
        case False:
            return severity_icon(fail_severity)
        case None:
            return "\u2014"


def build_mount_status_table(
    statuses: Sequence[tuple[str, MountStatusData]],
    *,
    title: str = "Volume Mount Status:",
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Table:
    """Build a Rich table showing mount status for each volume.

    ``False`` cells are disambiguated by ``mount_failure_reason`` via
    the shared ``{device,luks,mounted}_fail_severity`` helpers, which
    are also used by ``preflight.output.formatting.format_mount_status``
    so the two displays stay in sync.
    """
    table = Table(title=title)
    table.add_column("Name", style="bold")
    table.add_column("Device")
    table.add_column("Unlocked")
    table.add_column("Mounted")
    for slug, status in statuses:
        reason = status.mount_failure_reason
        table.add_row(
            slug,
            mount_state_icon(
                status.device_present,
                fail_severity=device_fail_severity(strictness),
            ),
            mount_state_icon(
                status.luks_unlocked,
                fail_severity=luks_fail_severity(reason, strictness),
            ),
            mount_state_icon(
                status.mounted,
                fail_severity=mounted_fail_severity(reason, strictness),
            ),
        )
    return table


def build_mount_status_json(
    statuses: Sequence[tuple[str, MountStatusData]],
) -> list[dict[str, object]]:
    """Build a JSON-serializable list of mount status entries."""
    return [
        {
            "volume": slug,
            "device_present": status.device_present,
            "luks_unlocked": status.luks_unlocked,
            "mounted": status.mounted,
        }
        for slug, status in statuses
    ]
