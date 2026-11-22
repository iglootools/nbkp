"""Render ``disks`` status, mount, umount and plan results as tables, text and JSON."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict

from rich.table import Table
from rich.text import Text

from nbkp.clidisplay import (
    dry_run_labeled,
    severity_icon,
    severity_style,
    severity_symbol,
)
from nbkp.disks.lifecycle import MountResult, UmountResult
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountFailureKind,
    UmountFailureKind,
)
from nbkp.disks.plan import VolumePlan
from nbkp.disks.severity import (
    device_fail_severity,
    luks_fail_severity,
    mounted_fail_severity,
)
from nbkp.disks.status import MountStatusData, VolumeStatus, VolumeStatusKind
from nbkp.policy import Severity, Strictness

# A row's display label.  ``Text`` when the label carries styling *and*
# caller-supplied text (an error detail, a "not managed" marker): Rich parses
# markup in a plain ``str``, so a detail containing ``[...]`` — udisksctl
# stderr, an exception message — would lose it.
type MountStatusLabel = str | Text


def mount_state_icon(
    *,
    value: bool | None,
    fail_severity: Severity = Severity.ERROR,
) -> str:
    """
    Format a mount state value as checkmark, cross/warning, or dash.

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
    """
    Build a Rich table showing mount status for each volume.

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
                value=status.device_present,
                fail_severity=device_fail_severity(strictness),
            ),
            mount_state_icon(
                value=status.luks_unlocked,
                fail_severity=luks_fail_severity(reason, strictness),
            ),
            mount_state_icon(
                value=status.mounted,
                fail_severity=mounted_fail_severity(reason, strictness),
            ),
        )
    return table


def error_label(name: str, detail: str | None) -> Text:
    """
    Fold an error detail into the volume's display name (red).

    Assembled as ``Text`` rather than a markup string: *detail* is external
    text (an exception message, udisksctl's stderr) that Rich would otherwise
    parse for ``[...]`` style tags.
    """
    if detail is None:
        return Text(name)
    else:
        return Text.assemble(
            name,
            " ",
            (
                f"{severity_symbol(Severity.ERROR)} {detail}",
                severity_style(Severity.ERROR),
            ),
        )


def volume_status_detail(status: VolumeStatus) -> str | None:
    """Why a volume was not probed, as one line; ``None`` when it was."""
    match status.kind:
        case VolumeStatusKind.PROBED:
            return None
        case VolumeStatusKind.UNREACHABLE:
            return f"unreachable: {status.error_message}"
        case VolumeStatusKind.TOOL_NOT_INSTALLED:
            return f"{status.missing_tool} not installed"
        case VolumeStatusKind.NOT_MANAGED:
            return "not managed"
        case VolumeStatusKind.LOCATION_EXCLUDED:
            return "location excluded"


def format_volume_status_label(name: str, status: VolumeStatus) -> Text:
    """
    Format the Name cell: the display name, with why the volume was not probed.

    Dim when it was never meant to be, red when probing failed.
    """
    match status.kind, volume_status_detail(status):
        case VolumeStatusKind.NOT_MANAGED | VolumeStatusKind.LOCATION_EXCLUDED, str(
            note,
        ):
            return Text.assemble(name, " ", (f"({note})", "dim"))
        case _, detail:
            return error_label(name, detail)


def build_volume_status_table(
    statuses: Sequence[VolumeStatus],
    display_names: Mapping[str, str],
) -> Table:
    """
    Return the ``disks status`` table, one row per volume.

    Rows are labelled from *display_names* (keyed by slug), falling back to
    the slug.
    """
    return build_mount_status_table(
        [
            (
                format_volume_status_label(
                    display_names.get(status.volume_slug, status.volume_slug),
                    status,
                ),
                status.state,
            )
            for status in statuses
        ],
    )


def volume_status_json(status: VolumeStatus) -> dict[str, object]:
    """
    Return a volume's ``-o json`` status entry.

    Its slug, whether it was probed (``status``) and why not, and the state observed.
    """
    return {
        "volume": status.volume_slug,
        "status": status.kind.value,
        "device_present": status.state.device_present,
        "luks_unlocked": status.state.luks_unlocked,
        "mounted": status.state.mounted,
        **{
            key: value
            for key, value in {
                "error": status.error_message,
                "missing_tool": status.missing_tool,
            }.items()
            if value is not None
        },
    }


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


def _mount_failure_detail(reason: MountFailureKind, failure: LifecycleFailure) -> str:
    match reason, failure:
        case MountFailureKind.DEVICE_NOT_PRESENT, _:
            return f"device not plugged in (UUID: {failure.device_uuid})"
        case MountFailureKind.PASSPHRASE_NOT_AVAILABLE, _:
            return (
                f"passphrase '{failure.passphrase_id}' not available: {failure.message}"
            )
        case MountFailureKind.NOT_AUTHORIZED, _:
            return f"{_step(failure)} not authorized — polkit rule not configured"
        case MountFailureKind.UDISKS_NOT_AVAILABLE, LifecycleFailure(step=None):
            return f"udisks2 not available: {failure.command} not found"
        case MountFailureKind.UDISKS_NOT_AVAILABLE, _:
            return f"{_step(failure)} failed — udisks2 not available: {failure.message}"
        case MountFailureKind.PROBE_FAILED, _:
            return _probe_failed(failure)
        case MountFailureKind.UNREACHABLE, _:
            return f"unreachable: {failure.message}"
        case MountFailureKind.UNLOCK_FAILED, LifecycleFailure(exit_code=None):
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


def _umount_failure_detail(reason: UmountFailureKind, failure: LifecycleFailure) -> str:
    match reason:
        case UmountFailureKind.PROBE_FAILED:
            return _probe_failed(failure)
        case UmountFailureKind.UNREACHABLE:
            return f"unreachable: {failure.message}"
        case UmountFailureKind.UDISKS_NOT_AVAILABLE:
            return f"udisks2 not available: {failure.command} not found"
        case UmountFailureKind.UNMOUNT_FAILED | UmountFailureKind.LOCK_FAILED:
            return _step_failed(failure)


def umount_failure_warning(result: UmountResult) -> str | None:
    """Describe what a failed umount leaves behind for the operator to clean up."""
    match result.failure_reason:
        case None:
            return None
        case UmountFailureKind.UNMOUNT_FAILED:
            return "device may still be mounted, manual umount needed"
        case UmountFailureKind.LOCK_FAILED:
            return "LUKS container still unlocked, manual lock needed"
        case UmountFailureKind.PROBE_FAILED:
            return "volume may still be mounted or unlocked, check manually"
        case UmountFailureKind.UNREACHABLE | UmountFailureKind.UDISKS_NOT_AVAILABLE:
            return "volume may still be mounted, manual umount needed"


def _failure_json(failure: LifecycleFailure) -> dict[str, object]:
    return {
        key: value.value if isinstance(value, LifecycleStep) else value
        for key, value in asdict(failure).items()
        if value is not None
    }


def mount_result_json(result: MountResult) -> dict[str, object]:
    """
    Return why a mount failed, for ``-o json``.

    The reason, the rendered detail and the facts behind it. Empty for a successful
    mount.
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


def format_plan_line(plan: VolumePlan, name: str) -> Text:
    """
    Format one labeled dry-run line.

    ``Would unlock, mount <name>``, ``Nothing to do``, or ``Cannot plan`` when the state
    could not be probed.
    """
    severity = Severity.ERROR if plan.probe_failed else Severity.OK
    verbs = ", ".join(action.value for action in plan.actions)
    return dry_run_labeled(
        Text.assemble(
            (severity_symbol(severity), severity_style(severity)),
            (
                f" Cannot plan {name}"
                if plan.probe_failed
                else f" Would {verbs} {name}"
                if plan.actions
                else f" Nothing to do for {name}"
            ),
            *([(f" ({plan.detail})", "dim")] if plan.detail else []),
        ),
    )
