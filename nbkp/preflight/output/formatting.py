"""Shared formatting helpers for preflight output."""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

from ...clihelpers import (
    OK_SYMBOL,
    Severity,
    severity_style,
    severity_symbol,
)
from ...disks.output import (
    device_fail_severity,
    luks_fail_severity,
    mounted_fail_severity,
)
from ..severity import PreflightError, severity_for_error, severity_for_errors
from ..status import (
    DestinationEndpointError,
    MountCapabilities,
    SourceEndpointError,
    SshEndpointError,
    SshEndpointStatus,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeError,
    VolumeStatus,
)
from ..strictness import Strictness
from ...config import MountConfig


def join_text(items: Iterable[Text], separator: str = ", ") -> Text:
    """Join styled Text fragments with a plain separator."""
    result = Text()
    for i, item in enumerate(items):
        if i > 0:
            result.append(separator)
        result.append_text(item)
    return result


def status_text(
    active: bool,
    errors: (
        list[VolumeError]
        | list[SyncError]
        | list[SshEndpointError]
        | list[SourceEndpointError]
        | list[DestinationEndpointError]
    ),
    strictness: Strictness,
) -> Text:
    """Format status with optional errors as styled text.

    Inactive statuses use the warning icon when their errors are all
    non-fatal under *strictness* (e.g. missing sentinels with the
    default ``IGNORE_INACTIVE``), and the error icon otherwise.
    """
    if active:
        return Text(f"{OK_SYMBOL}active", style=severity_style(Severity.OK))
    severity = severity_for_errors(errors, strictness)
    error_str = ", ".join(r.value for r in errors)
    return Text(
        f"{severity_symbol(severity)}inactive ({error_str})",
        style=severity_style(severity),
    )


def mount_capability_items(
    mount: MountCapabilities,
) -> list[tuple[bool | None, str | None]]:
    """Build capability display items for a mount-managed volume.

    udisks has no backend/strategy distinction, so we surface the
    effective mountpoint (the path udisks mounted at, or the discovered
    cleartext device) instead of a systemd mount-unit name.
    """
    return [
        (
            mount.effective_path is not None,
            f"mount:{mount.effective_path}" if mount.effective_path else None,
        ),
        (
            mount.cleartext_device is not None,
            f"cleartext:{mount.cleartext_device}" if mount.cleartext_device else None,
        ),
    ]


def format_capabilities(caps: VolumeCapabilities | None) -> Text:
    """Format volume capabilities as styled text."""
    if caps is None:
        return Text("")
    items = [
        label
        for flag, label in [
            (caps.sentinel_exists, "sentinel"),
            (caps.is_btrfs_filesystem, "btrfs-fs"),
            (caps.hardlink_supported, "hardlink"),
            (caps.btrfs_user_subvol_rm, "user_subvol_rm"),
            *(mount_capability_items(caps.mount) if caps.mount is not None else []),
        ]
        if flag and label is not None
    ]
    return Text(", ".join(items) if items else "none")


def check(
    ok: bool,
    label: str,
    fail_error: PreflightError | None = None,
    fail_severity: Severity | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Text:
    """Format a diagnostic item as \u2713label (green) or \u26a0/\u2717label.

    The failure icon is picked from (in order of priority):

    - *fail_severity* if given \u2014 caller has already classified the
      failure (e.g. mount-state columns use shared helpers).
    - Otherwise, *fail_error* classified via ``severity_for_error``
      under *strictness*.
    - Otherwise, ``Severity.ERROR``.
    """
    if ok:
        return Text(f"{OK_SYMBOL}{label}", style=severity_style(Severity.OK))
    if fail_severity is not None:
        severity = fail_severity
    elif fail_error is not None:
        severity = severity_for_error(fail_error, strictness)
    else:
        severity = Severity.ERROR
    return Text(
        f"{severity_symbol(severity)}{label}",
        style=severity_style(severity),
    )


def format_mount_status(
    mount_caps: MountCapabilities | None,
    mount_config: MountConfig | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Text:
    """Format runtime mount state as styled text.

    Shows \u2713/\u26a0/\u2717 for each probed mount state item.  Items whose value
    is ``None`` (not probed / not applicable) are omitted, matching
    the pattern used by source/destination diagnostics columns.
    Empty when the volume has no mount config or caps are unavailable.

    Failure-icon severity is delegated to the shared
    ``{device,luks,mounted}_fail_severity`` helpers in
    :mod:`nbkp.disks.output`, which inspect ``mount_failure_reason`` to
    distinguish real failures (\u2717) from observation/cascade states (\u26a0).
    The "drive not plugged in \u2192 \u26a0 luks" cascade emerges naturally:
    when no LUKS-unlock attempt was made the failure_reason isn't in
    ``LUKS_STAGE_FAILURES``, so the helper returns the inactive-class
    severity for the active strictness.
    """
    if mount_caps is None or mount_config is None:
        return Text("")
    reason = mount_caps.mount_failure_reason
    items: list[tuple[bool | None, str, Severity]] = [
        (mount_caps.device_present, "device", device_fail_severity(strictness)),
        *(
            [(mount_caps.luks_unlocked, "luks", luks_fail_severity(reason, strictness))]
            if mount_config.encryption is not None
            else []
        ),
        (mount_caps.mounted, "mounted", mounted_fail_severity(reason, strictness)),
    ]
    return join_text(
        check(bool(value), label, fail_severity=fail_sev)
        for value, label, fail_sev in items
        if value is not None
    )


def collect_ssh_endpoint_statuses(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
) -> dict[str, SshEndpointStatus]:
    """Collect unique SSH endpoint statuses from volumes and sync statuses.

    SSH endpoint statuses are embedded in volume statuses.  This extracts
    them for display in the SSH Endpoints table.  First-seen wins for
    duplicate slugs.
    """
    all_ssh = [
        *[vs.ssh_endpoint_status for vs in vol_statuses.values()],
        *[
            ep.volume_status.ssh_endpoint_status
            for ss in sync_statuses.values()
            for ep in [ss.source_endpoint_status, ss.destination_endpoint_status]
        ],
    ]
    # dict.fromkeys-style: first occurrence wins
    return dict({s.slug: s for s in reversed(all_ssh)})
