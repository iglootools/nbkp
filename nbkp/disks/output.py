from collections.abc import Sequence
from dataclasses import asdict
from typing import Protocol

from rich.table import Table
from rich.text import Text

from ..clidisplay import severity_icon
from ..policy import Severity, Strictness
from .lifecycle import MountResult, UmountResult
from .models import (
    LifecycleFailure,
    LifecycleStep,
    MountFailureReason,
    UmountFailureReason,
)
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


def _step(failure: LifecycleFailure) -> str:
    return failure.step.value if failure.step is not None else "command"


def _step_failed(failure: LifecycleFailure) -> str:
    return (
        f"{_step(failure)} failed (exit {failure.exit_code}): {failure.message or ''}"
    ).rstrip()


def _probe_failed(failure: LifecycleFailure) -> str:
    return (
        f"device state unknown: {failure.command} failed"
        f" (exit {failure.exit_code}): {failure.message or ''}"
    ).rstrip()


def mount_failure_detail(result: MountResult) -> str | None:
    """Why a mount failed, as one line; ``None`` when it did not."""
    reason, failure = result.failure_reason, result.failure
    return (
        _mount_failure_detail(reason, failure)
        if reason is not None and failure is not None
        else None
    )


def _mount_failure_detail(reason: MountFailureReason, failure: LifecycleFailure) -> str:
    match reason, failure:
        case MountFailureReason.DEVICE_NOT_PRESENT, _:
            return f"device not plugged in (UUID: {failure.device_uuid})"
        case MountFailureReason.PASSPHRASE_NOT_AVAILABLE, _:
            return (
                f"passphrase '{failure.passphrase_id}' not available: {failure.message}"
            )
        case MountFailureReason.NOT_AUTHORIZED, _:
            return f"{_step(failure)} not authorized — polkit rule not configured"
        case MountFailureReason.UDISKS_NOT_AVAILABLE, LifecycleFailure(step=None):
            return f"udisks2 not available: {failure.command} not found"
        case MountFailureReason.UDISKS_NOT_AVAILABLE, _:
            return f"{_step(failure)} failed — udisks2 not available: {failure.message}"
        case MountFailureReason.PROBE_FAILED, _:
            return _probe_failed(failure)
        case MountFailureReason.UNREACHABLE, _:
            return f"unreachable: {failure.message}"
        case MountFailureReason.UNLOCK_FAILED, LifecycleFailure(exit_code=None):
            return "unlocked device could not be resolved"
        case _:
            return _step_failed(failure)


def umount_failure_detail(result: UmountResult) -> str | None:
    """Why an umount failed, as one line; ``None`` when it did not."""
    reason, failure = result.failure_reason, result.failure
    return (
        _umount_failure_detail(reason, failure)
        if reason is not None and failure is not None
        else None
    )


def _umount_failure_detail(
    reason: UmountFailureReason, failure: LifecycleFailure
) -> str:
    match reason:
        case UmountFailureReason.PROBE_FAILED:
            return _probe_failed(failure)
        case UmountFailureReason.UNREACHABLE:
            return f"unreachable: {failure.message}"
        case UmountFailureReason.UDISKS_NOT_AVAILABLE:
            return f"udisks2 not available: {failure.command} not found"
        case UmountFailureReason.UNMOUNT_FAILED | UmountFailureReason.LOCK_FAILED:
            return _step_failed(failure)


def umount_failure_warning(result: UmountResult) -> str | None:
    """What a failed umount leaves behind for the operator to clean up."""
    match result.failure_reason:
        case None:
            return None
        case UmountFailureReason.UNMOUNT_FAILED:
            return "device may still be mounted, manual umount needed"
        case UmountFailureReason.LOCK_FAILED:
            return "LUKS container still unlocked, manual lock needed"
        case UmountFailureReason.PROBE_FAILED:
            return "volume may still be mounted or unlocked, check manually"
        case UmountFailureReason.UNREACHABLE | UmountFailureReason.UDISKS_NOT_AVAILABLE:
            return "volume may still be mounted, manual umount needed"


def _failure_json(failure: LifecycleFailure) -> dict[str, object]:
    return {
        key: value.value if isinstance(value, LifecycleStep) else value
        for key, value in asdict(failure).items()
        if value is not None
    }


def mount_result_json(result: MountResult) -> dict[str, object]:
    """Why a mount failed, for ``-o json``: the reason, the rendered detail and
    the facts behind it.  Empty for a successful mount.
    """
    return (
        {
            "failure_reason": result.failure_reason.value,
            "detail": mount_failure_detail(result),
            "failure": _failure_json(result.failure),
        }
        if result.failure_reason is not None and result.failure is not None
        else {}
    )


def umount_result_json(result: UmountResult) -> dict[str, object]:
    """Like :func:`mount_result_json`, plus what the failure leaves behind."""
    return (
        {
            "failure_reason": result.failure_reason.value,
            "detail": umount_failure_detail(result),
            "warning": umount_failure_warning(result),
            "failure": _failure_json(result.failure),
        }
        if result.failure_reason is not None and result.failure is not None
        else {}
    )
