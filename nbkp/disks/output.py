"""Mount status display helpers (Rich tables and JSON)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rich.table import Table
from rich.text import Text

from ..clihelpers import severity_icon
from ..policy import Severity, Strictness
from .models import MountFailureReason
from .severity import device_fail_severity, luks_fail_severity, mounted_fail_severity

# A row's display label.  ``Text`` when the label carries styling *and*
# caller-supplied text (an error detail, a "not managed" marker): Rich parses
# markup in a plain ``str``, so a detail containing ``[...]`` — udisksctl
# stderr, an exception message — would lose it.  ``str(label)`` recovers the
# plain text for JSON either way.
MountStatusLabel = str | Text


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
    def mount_failure_reason(self) -> MountFailureReason | None: ...


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
    statuses: Sequence[tuple[MountStatusLabel, MountStatusData]],
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
    statuses: Sequence[tuple[MountStatusLabel, MountStatusData]],
) -> list[dict[str, object]]:
    """Build a JSON-serializable list of mount status entries.

    ``str(slug)`` rather than ``slug``: a ``Text`` label renders to its plain
    content, so annotated labels reach JSON as readable text instead of the
    Rich markup they used to leak (``vol [dim](not managed)[/dim]``).
    """
    return [
        {
            "volume": str(slug),
            "device_present": status.device_present,
            "luks_unlocked": status.luks_unlocked,
            "mounted": status.mounted,
        }
        for slug, status in statuses
    ]
