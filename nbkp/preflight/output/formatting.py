"""Shared formatting helpers for preflight output."""

from __future__ import annotations

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


def volume_status_text(vs: VolumeStatus) -> Text:
    """Format volume status, cascading SSH endpoint errors when the volume
    itself has none but is inactive due to its SSH endpoint."""
    if vs.active:
        return Text("\u2713active", style="green")
    errors = [
        *[f"ssh: {e.value}" for e in vs.ssh_endpoint_status.errors],
        *[e.value for e in vs.errors],
    ]
    error_str = ", ".join(errors)
    return Text(f"\u2717inactive ({error_str})", style="red")


def all_sync_errors(ss: SyncStatus) -> list[str]:
    """Collect error values from all 4 layers for a sync.

    Returns a flat list of error value strings for display.
    """
    src_ep = ss.source_endpoint_status
    dst_ep = ss.destination_endpoint_status
    return [
        *(e.value for e in src_ep.volume_status.ssh_endpoint_status.errors),
        *(e.value for e in dst_ep.volume_status.ssh_endpoint_status.errors),
        *(e.value for e in src_ep.volume_status.errors),
        *(e.value for e in dst_ep.volume_status.errors),
        *(e.value for e in src_ep.errors),
        *(e.value for e in dst_ep.errors),
        *(e.value for e in ss.errors),
    ]


# Errors from lower layers that are visible as diagnostics columns,
# so they don't need to be repeated in the sync Status column.
DIAGNOSTIC_VISIBLE_ERRORS: frozenset[str] = frozenset(
    {
        # Volume-level — shown as volume(reason) in src/dst diagnostics
        e.value
        for e in [
            VolumeError.SENTINEL_NOT_FOUND,
            VolumeError.VOLUME_NOT_MOUNTED,
            VolumeError.DEVICE_NOT_PRESENT,
        ]
    }
    | {
        # SSH endpoint — shown as volume(reason) in src/dst diagnostics
        e.value
        for e in [
            SshEndpointError.UNREACHABLE,
            SshEndpointError.LOCATION_EXCLUDED,
        ]
    }
    | {
        # Source endpoint — shown as check items in src diagnostics
        e.value
        for e in [
            SourceEndpointError.SENTINEL_NOT_FOUND,
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND,
            SourceEndpointError.LATEST_SYMLINK_INVALID,
            SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
        ]
    }
    | {
        # Destination endpoint — shown as check items in dst diagnostics
        e.value
        for e in [
            DestinationEndpointError.SENTINEL_NOT_FOUND,
            DestinationEndpointError.NOT_WRITABLE,
            DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME,
            DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND,
            DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
            DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND,
            DestinationEndpointError.LATEST_SYMLINK_INVALID,
            DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE,
            DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE,
            DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT,
        ]
    }
)


def sync_status_text(ss: SyncStatus) -> Text:
    """Format sync status, showing only non-obvious error reasons.

    Errors that are visible as check items in the diagnostics columns are
    omitted from the status text to avoid redundancy.  Non-obvious
    errors (disabled, tool-version, dry-run) are shown.  When all errors
    are diagnostic-visible, falls back to the full error list so the
    status column is never a bare "✗inactive" with no explanation.
    """
    if ss.active:
        return Text("\u2713active", style="green")
    errors_all = all_sync_errors(ss)
    non_obvious = [e for e in errors_all if e not in DIAGNOSTIC_VISIBLE_ERRORS]
    errors = non_obvious if non_obvious else errors_all
    # Deduplicate while preserving order (same SSH endpoint error
    # can appear via both source and destination paths)
    seen: set[str] = set()
    unique = [e for e in errors if not (e in seen or seen.add(e))]  # type: ignore[func-returns-value]
    reason = ", ".join(unique)
    return Text(f"\u2717inactive ({reason})", style="red")


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


def format_capabilities(caps: VolumeCapabilities | None) -> str:
    """Format volume capabilities as a compact comma-separated string."""
    if caps is None:
        return ""
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
    return ", ".join(items) if items else "none"


def check(ok: bool, label: str) -> str:
    """Format a diagnostic item as check-label or x-label with color."""
    return f"[green]\u2713{label}[/green]" if ok else f"[red]\u2717{label}[/red]"


def format_mount_status(
    mount_caps: MountCapabilities | None,
    mount_config: MountConfig | None,
) -> str:
    """Format runtime mount state as a compact string.

    Shows check/x for each probed mount state item.  Items whose value
    is ``None`` (not probed / not applicable) are omitted, matching
    the pattern used by source/destination diagnostics columns.
    Empty when the volume has no mount config or caps are unavailable.
    """
    if mount_caps is None or mount_config is None:
        return ""
    items = [
        (mount_caps.device_present, "device"),
        *(
            [(mount_caps.luks_attached, "luks")]
            if mount_config.encryption is not None
            else []
        ),
        (mount_caps.mounted, "mounted"),
    ]
    return ", ".join(
        check(value, label) for value, label in items if value is not None
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
