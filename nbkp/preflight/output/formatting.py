"""Shared formatting helpers for preflight output."""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

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
) -> Text:
    """Format status with optional errors as styled text."""
    if active:
        return Text("\u2713active", style="green")
    else:
        error_str = ", ".join(r.value for r in errors)
        return Text(f"\u2717inactive ({error_str})", style="red")


def mount_capability_items(
    mount: MountCapabilities,
) -> list[tuple[bool | None, str | None]]:
    """Build capability display items based on resolved backend."""
    match mount.resolved_backend:
        case "direct":
            return [
                (True, "mnt-strategy:direct"),
                (mount.has_sudoers_rules, "sudoers"),
            ]
        case _:
            return [
                (
                    True,
                    f"mnt-strategy:{mount.resolved_backend or 'systemd'}",
                ),
                (
                    mount.mount_unit is not None,
                    f"mount:{mount.mount_unit}" if mount.mount_unit else None,
                ),
                (mount.has_polkit_rules, "polkit"),
                (mount.has_sudoers_rules, "sudoers"),
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


def check(ok: bool, label: str) -> Text:
    """Format a diagnostic item as check-label or x-label with color."""
    if ok:
        return Text(f"\u2713{label}", style="green")
    else:
        return Text(f"\u2717{label}", style="red")


def format_mount_status(
    mount_caps: MountCapabilities | None,
    mount_config: MountConfig | None,
) -> Text:
    """Format runtime mount state as styled text.

    Shows check/x for each probed mount state item.  Items whose value
    is ``None`` (not probed / not applicable) are omitted, matching
    the pattern used by source/destination diagnostics columns.
    Empty when the volume has no mount config or caps are unavailable.
    """
    if mount_caps is None or mount_config is None:
        return Text("")
    items = [
        (mount_caps.device_present, "device"),
        *(
            [(mount_caps.luks_attached, "luks")]
            if mount_config.encryption is not None
            else []
        ),
        (mount_caps.mounted, "mounted"),
    ]
    return join_text(check(value, label) for value, label in items if value is not None)


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
