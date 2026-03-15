"""Preflight check output formatting."""

from __future__ import annotations

import getpass
from textwrap import dedent

from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..config import (
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
)
from ..config.output import (
    _sync_endpoint_display,
    _sync_options,
    endpoint_path,
    format_mount_summary,
    format_volume_display,
    host_label,
)
from ..mount.auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH, generate_auth_rules
from ..remote.ssh import (
    format_proxy_jump_chain,
    ssh_prefix,
    wrap_cmd,
)
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from .status import (
    MountCapabilities,
    SyncError,
    SyncStatus,
    VolumeCapabilities,
    VolumeError,
    VolumeStatus,
)


def _status_text(
    active: bool,
    errors: list[VolumeError] | list[SyncError],
) -> Text:
    """Format status with optional errors as styled text."""
    if active:
        return Text("\u2713active", style="green")
    else:
        error_str = ", ".join(r.value for r in errors)
        return Text(f"\u2717inactive ({error_str})", style="red")


# Sync errors whose diagnostic state is visible as ✓/✗ in the
# Src/Dst Diagnostics columns.  When *all* errors for a sync are
# diagnostic-visible, the Status column shows a plain "inactive"
# because the diagnostics already tell the story.  Non-obvious
# errors (disabled, unavailable, tool-version, dry-run) are always
# shown in the Status column so they aren't silently hidden.
_DIAGNOSTIC_VISIBLE_SYNC_ERRORS: frozenset[SyncError] = frozenset(
    {
        # Volume-level — shown as ✗volume(...) in src/dst diagnostics
        SyncError.SRC_VOL_UNAVAILABLE,
        SyncError.DST_VOL_UNAVAILABLE,
        # Endpoint-level — shown as ✓/✗ items in src/dst diagnostics
        SyncError.SRC_EP_SENTINEL_NOT_FOUND,
        SyncError.SRC_EP_LATEST_SYMLINK_NOT_FOUND,
        SyncError.SRC_EP_LATEST_SYMLINK_INVALID,
        SyncError.SRC_EP_SNAPSHOTS_DIR_NOT_FOUND,
        SyncError.DST_EP_SENTINEL_NOT_FOUND,
        SyncError.DST_EP_NOT_WRITABLE,
        SyncError.DST_EP_STAGING_NOT_BTRFS_SUBVOLUME,
        SyncError.DST_EP_STAGING_SUBVOL_NOT_FOUND,
        SyncError.DST_EP_SNAPSHOTS_DIR_NOT_FOUND,
        SyncError.DST_EP_LATEST_SYMLINK_NOT_FOUND,
        SyncError.DST_EP_LATEST_SYMLINK_INVALID,
        SyncError.DST_EP_SNAPSHOTS_DIR_NOT_WRITABLE,
        SyncError.DST_EP_STAGING_SUBVOL_NOT_WRITABLE,
        SyncError.DST_VOL_NO_HARDLINK_SUPPORT,
    }
)


def _sync_status_text(
    active: bool,
    errors: list[SyncError],
) -> Text:
    """Format sync status, showing only non-obvious error reasons.

    Errors that are visible as ✓/✗ in the diagnostics columns are
    omitted from the status text to avoid redundancy.  Non-obvious
    errors (disabled, unavailable, tool-version, dry-run) are shown.
    """
    if active:
        return Text("\u2713active", style="green")
    non_obvious = [e for e in errors if e not in _DIAGNOSTIC_VISIBLE_SYNC_ERRORS]
    if non_obvious:
        reason = ", ".join(e.value for e in non_obvious)
        return Text(f"\u2717inactive ({reason})", style="red")
    else:
        return Text("\u2717inactive", style="red")


def _mount_capability_items(
    mount: MountCapabilities,
) -> list[tuple[bool | None, str | None]]:
    """Build capability display items based on resolved backend."""
    match mount.resolved_backend:
        case "direct":
            return [
                (True, "mnt-strategy:direct"),
                (mount.has_sudo, "sudo"),
                (mount.has_mount_cmd, "mount"),
                (mount.has_umount_cmd, "umount"),
                (mount.has_mountpoint, "mountpoint"),
                (mount.has_cryptsetup, "cryptsetup"),
                (mount.has_sudoers_rules, "sudoers"),
            ]
        case _:
            return [
                (
                    True,
                    f"mnt-strategy:{mount.resolved_backend or 'systemd'}",
                ),
                (mount.has_systemctl, "systemctl"),
                (mount.has_systemd_escape, "systemd-escape"),
                (mount.has_sudo, "sudo"),
                (mount.has_cryptsetup, "cryptsetup"),
                (mount.has_systemd_cryptsetup, "systemd-cryptsetup"),
                (
                    mount.mount_unit is not None,
                    f"mount:{mount.mount_unit}" if mount.mount_unit else None,
                ),
                (mount.has_polkit_rules, "polkit"),
                (mount.has_sudoers_rules, "sudoers"),
            ]


def _format_capabilities(caps: VolumeCapabilities | None) -> str:
    """Format volume capabilities as a compact comma-separated string."""
    if caps is None:
        return ""
    items = [
        label
        for flag, label in [
            (caps.has_rsync, "rsync 3.0+" if caps.rsync_version_ok else "rsync (old)"),
            (caps.has_btrfs, "btrfs"),
            (caps.has_stat, "stat"),
            (caps.has_findmnt, "findmnt"),
            (caps.is_btrfs_filesystem, "btrfs-fs"),
            (caps.hardlink_supported, "hardlink"),
            (caps.btrfs_user_subvol_rm, "user_subvol_rm"),
            *(_mount_capability_items(caps.mount) if caps.mount is not None else []),
        ]
        if flag and label is not None
    ]
    return ", ".join(items) if items else "none"


def _check(ok: bool, label: str) -> str:
    """Format a diagnostic item as ✓label or ✗label with color."""
    return f"[green]\u2713{label}[/green]" if ok else f"[red]\u2717{label}[/red]"


def _format_mount_status(
    mount_caps: MountCapabilities | None,
    mount_config: MountConfig | None,
) -> str:
    """Format runtime mount state as a compact string.

    Shows ✓/✗ for each probed mount state item.  Items whose value
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
        _check(value, label) for value, label in items if value is not None
    )


def _format_volume_issues(vol_status: VolumeStatus) -> str:
    """Format volume-level issues as ✗volume(reason)."""
    reason = ", ".join(e.value for e in vol_status.errors)
    return (
        f"[red]\u2717volume ({reason})[/red]" if reason else "[red]\u2717volume[/red]"
    )


def _format_source_diagnostics(ss: SyncStatus) -> str:
    """Format source endpoint diagnostics as a compact comma-separated string.

    When the source volume is inactive, shows ✗volume(reason) instead
    of endpoint-level items (which weren't computed).  Otherwise shows
    ✓/✗ for each checked item.  Items only appear when the
    corresponding feature is configured (e.g. snapshots/ and latest
    are omitted when the endpoint has no snapshot mode).
    """
    if not ss.source_status.active:
        return _format_volume_issues(ss.source_status)
    diag = ss.source_diagnostics
    if diag is None:
        return ""
    items = [
        _check(diag.sentinel_exists, "sentinel"),
        *(
            [_check(diag.snapshot_dirs.exists, "snapshots/")]
            if diag.snapshot_dirs is not None
            else []
        ),
        *(
            [
                f"[green]\u2713latest \u2192 {diag.latest.raw_target}[/green]"
                if diag.latest.exists and diag.latest.raw_target
                else "[red]\u2717latest[/red]"
            ]
            if diag.latest is not None
            else []
        ),
    ]
    return ", ".join(items)


def _format_destination_diagnostics(ss: SyncStatus) -> str:
    """Format destination endpoint diagnostics as a compact comma-separated string.

    When the destination volume is inactive, shows ✗volume(reason)
    instead of endpoint-level items.  Otherwise shows ✓/✗ for each
    checked item.  Btrfs-specific items (subvolume, staging/) only
    appear when btrfs diagnostics are present.  Snapshot items only
    appear when snapshot mode is configured.
    """
    if not ss.destination_status.active:
        return _format_volume_issues(ss.destination_status)
    diag = ss.destination_diagnostics
    if diag is None:
        return ""
    items = [
        _check(diag.sentinel_exists, "sentinel"),
        _check(diag.endpoint_writable, "writable"),
        *(
            [
                _check(diag.btrfs.staging_exists, "staging/"),
                _check(diag.btrfs.staging_is_subvolume, "staging-subvolume"),
            ]
            if diag.btrfs is not None
            else []
        ),
        *(
            [_check(diag.snapshot_dirs.exists, "snapshots/")]
            if diag.snapshot_dirs is not None
            else []
        ),
        *(
            [
                f"[green]\u2713latest \u2192 {diag.latest.raw_target}[/green]"
                if diag.latest.exists and diag.latest.raw_target
                else "[red]\u2717latest[/red]"
            ]
            if diag.latest is not None
            else []
        ),
    ]
    return ", ".join(items)


def _build_ssh_endpoints_section(
    config: Config,
) -> list[RenderableType]:
    """Build the SSH Endpoints table section."""
    if not config.ssh_endpoints:
        return []
    table = Table(title="SSH Endpoints:")
    table.add_column("Name", style="bold")
    table.add_column("Host")
    table.add_column("Port")
    table.add_column("User")
    table.add_column("Key")
    table.add_column("Proxy Jump")
    table.add_column("Locations")

    for server in config.ssh_endpoints.values():
        table.add_row(
            server.slug,
            server.host,
            str(server.port),
            server.user or "",
            server.key or "",
            ", ".join(server.proxy_jump_chain) or "",
            ", ".join(server.location_list),
        )

    return [table, Text("")]


def _build_volumes_section(
    vol_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build the Volumes table section."""
    table = Table(title="Volumes:")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("SSH Endpoint")
    table.add_column("URI")
    table.add_column("Mount Config")
    table.add_column("Mount Diagnostics")
    table.add_column("Capabilities")
    table.add_column("Status")

    for vs in vol_statuses.values():
        vol = vs.config
        caps = vs.diagnostics.capabilities
        mount_caps = caps.mount if caps else None
        match vol:
            case RemoteVolume():
                vol_type = "remote"
                ep = resolved_endpoints.get(vol.slug)
                ssh_ep = ep.server.slug if ep else vol.ssh_endpoint
            case LocalVolume():
                vol_type = "local"
                ssh_ep = ""
        table.add_row(
            vs.slug,
            vol_type,
            ssh_ep,
            format_volume_display(vol, resolved_endpoints),
            format_mount_summary(vol.mount),
            _format_mount_status(mount_caps, vol.mount),
            _format_capabilities(caps),
            _status_text(vs.active, vs.errors),
        )

    return [table, Text("")]


def _build_syncs_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
) -> list[RenderableType]:
    """Build the Syncs table section."""
    table = Table(title="Syncs:")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Destination")
    table.add_column("Options")
    table.add_column("Src Diagnostics")
    table.add_column("Dst Diagnostics")
    table.add_column("Status")

    for ss in sync_statuses.values():
        table.add_row(
            ss.slug,
            _sync_endpoint_display(config.source_endpoint(ss.config)),
            _sync_endpoint_display(config.destination_endpoint(ss.config)),
            _sync_options(ss.config, config),
            _format_source_diagnostics(ss),
            _format_destination_diagnostics(ss),
            _sync_status_text(ss.active, ss.errors),
        )

    return [table]


def _build_orphan_warnings_section(
    config: Config,
) -> list[RenderableType]:
    """Build warnings for orphan config items."""
    warnings = [
        *[
            f"SSH endpoint '{slug}' is not referenced by any volume"
            for slug in config.orphan_ssh_endpoints()
        ],
        *[
            f"Volume '{slug}' is not referenced by any sync endpoint"
            for slug in config.orphan_volumes()
        ],
        *[
            f"Sync endpoint '{slug}' is not referenced by any sync"
            for slug in config.orphan_sync_endpoints()
        ],
    ]
    if not warnings:
        return []
    text = Text("\n").join(
        Text.assemble(("warning: ", "yellow bold"), warning) for warning in warnings
    )
    return [Text(""), text]


def build_check_sections(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build renderable sections for check output."""
    return [
        *_build_ssh_endpoints_section(config),
        *_build_volumes_section(vol_statuses, resolved_endpoints),
        *_build_syncs_section(sync_statuses, config),
        *_build_orphan_warnings_section(config),
    ]


def print_human_check(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    wrap_in_panel: bool = True,
) -> None:
    """Print human-readable status output."""
    re = resolved_endpoints or {}
    if console is None:
        console = Console()

    sections = build_check_sections(vol_statuses, sync_statuses, config, re)

    if wrap_in_panel:
        console.print(
            Panel(
                Group(*sections),
                title="[bold]Preflight Checks[/bold]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    else:
        for section in sections:
            console.print(section)


# ---------------------------------------------------------------------------
# Troubleshooting output
# ---------------------------------------------------------------------------

_INDENT = "  "

_RSYNC_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install rsync
    Fedora/RHEL:   sudo dnf install rsync
    macOS:         brew install rsync""")

_BTRFS_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install btrfs-progs
    Fedora/RHEL:   sudo dnf install btrfs-progs""")

_COREUTILS_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install coreutils
    Fedora/RHEL:   sudo dnf install coreutils""")

_UTIL_LINUX_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install util-linux
    Fedora/RHEL:   sudo dnf install util-linux""")


def _print_cmd(
    console: Console,
    cmd: str,
    indent: int = 2,
) -> None:
    """Print a shell command with bash syntax highlighting.

    ``indent`` is the number of ``_INDENT`` levels (each 2 spaces).
    """
    syntax = Syntax(
        cmd,
        "bash",
        theme="monokai",
        background_color="default",
    )
    pad = len(_INDENT) * indent
    console.print(Padding(syntax, (0, 0, 0, pad)))


def _print_sentinel_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    sentinel: str,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print sentinel creation fix with mount reminder."""
    p2 = _INDENT * 2
    console.print(f"{p2}Ensure the volume is mounted, then:")
    _print_cmd(
        console,
        wrap_cmd(f"mkdir -p {path}", vol, resolved_endpoints),
    )
    _print_cmd(
        console,
        wrap_cmd(
            f"touch {path}/{sentinel}",
            vol,
            resolved_endpoints,
        ),
    )


def _print_ssh_troubleshoot(
    console: Console,
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> None:
    """Print SSH connectivity troubleshooting instructions."""
    p2 = _INDENT * 2
    p3 = _INDENT * 3
    ssh_cmd = " ".join(ssh_prefix(server, proxy_chain))
    port_flag = f"-p {server.port} " if server.port != 22 else ""
    proxy_opt = ""
    if proxy_chain:
        jump_str = format_proxy_jump_chain(proxy_chain)
        proxy_opt = f"-o ProxyJump={jump_str} "
    user_host = f"{server.user}@{server.host}" if server.user else server.host
    console.print(f"{p2}Server {server.host} is unreachable.")
    console.print(f"{p2}Verify connectivity:")
    _print_cmd(console, f"{ssh_cmd} echo ok", indent=3)
    console.print(f"{p2}If authentication fails:")
    if server.key:
        console.print(f"{p3}1. Ensure the key exists:")
        _print_cmd(console, f"ls -l {server.key}", indent=4)
        console.print(f"{p3}2. Copy it to the server:")
        _print_cmd(
            console,
            f"ssh-copy-id {proxy_opt}{port_flag}-i {server.key} {user_host}",
            indent=4,
        )
    else:
        console.print(f"{p3}1. Generate a key:")
        _print_cmd(console, "ssh-keygen -t ed25519", indent=4)
        console.print(f"{p3}2. Copy it to the server:")
        _print_cmd(
            console,
            f"ssh-copy-id {proxy_opt}{port_flag}{user_host}",
            indent=4,
        )
    console.print(f"{p3}3. Verify passwordless login:")
    _print_cmd(console, f"{ssh_cmd} echo ok", indent=4)


def _print_sync_error_fix(
    console: Console,
    sync: SyncConfig,
    error: SyncError,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix instructions for a sync error."""
    p2 = _INDENT * 2
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    src_vol = config.volumes[src_ep.volume]
    dst_vol = config.volumes[dst_ep.volume]
    match error:
        case SyncError.DISABLED:
            console.print(f"{p2}Enable the sync in the configuration file.")
        case SyncError.SRC_VOL_UNAVAILABLE:
            match src_vol:
                case RemoteVolume():
                    ep = resolved_endpoints.get(src_vol.slug)
                    if ep is not None:
                        _print_ssh_troubleshoot(
                            console,
                            ep.server,
                            ep.proxy_chain,
                        )
                    else:
                        console.print(
                            f"{p2}Source volume"
                            f" '{src_ep.volume}'"
                            " excluded by location"
                            " filter."
                        )
                case LocalVolume():
                    console.print(
                        f"{p2}Source volume '{src_ep.volume}' is not available."
                    )
        case SyncError.DST_VOL_UNAVAILABLE:
            match dst_vol:
                case RemoteVolume():
                    ep = resolved_endpoints.get(dst_vol.slug)
                    if ep is not None:
                        _print_ssh_troubleshoot(
                            console,
                            ep.server,
                            ep.proxy_chain,
                        )
                    else:
                        console.print(
                            f"{p2}Destination volume"
                            f" '{dst_ep.volume}'"
                            " excluded by location"
                            " filter."
                        )
                case LocalVolume():
                    console.print(
                        f"{p2}Destination volume '{dst_ep.volume}' is not available."
                    )
        case SyncError.SRC_EP_SENTINEL_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            _print_sentinel_fix(
                console,
                src_vol,
                path,
                SOURCE_SENTINEL,
                resolved_endpoints,
            )
        case SyncError.SRC_EP_LATEST_SYMLINK_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    src_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.SRC_EP_LATEST_SYMLINK_INVALID:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Ensure the upstream"
                " sync has run at least once,"
                " or reset it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    src_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.SRC_EP_SNAPSHOTS_DIR_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            if src_ep.btrfs_snapshots.enabled:
                cmds = [
                    f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                ]
            else:
                cmds = [f"mkdir -p {path}/{SNAPSHOTS_DIR}"]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncError.DST_EP_SENTINEL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            _print_sentinel_fix(
                console,
                dst_vol,
                path,
                DESTINATION_SENTINEL,
                resolved_endpoints,
            )
        case SyncError.SRC_VOL_RSYNC_NOT_FOUND:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DST_VOL_RSYNC_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.SRC_VOL_RSYNC_TOO_OLD:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DST_VOL_RSYNC_TOO_OLD:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DST_VOL_BTRFS_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install btrfs-progs on {host}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SyncError.DST_VOL_STAT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install coreutils (stat) on {host}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SyncError.DST_VOL_FINDMNT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install util-linux (findmnt) on {host}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SyncError.DST_VOL_NOT_BTRFS:
            console.print(f"{p2}The destination is not on a btrfs filesystem.")
        case SyncError.DST_EP_STAGING_NOT_BTRFS_SUBVOLUME:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/{STAGING_DIR}",
                f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                "sudo chown <user>:<group>"
                f" {path}/{STAGING_DIR}"
                f" {path}/{SNAPSHOTS_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DST_VOL_NOT_MOUNTED_USER_SUBVOL_RM:
            console.print(f"{p2}Remount the btrfs volume with user_subvol_rm_allowed:")
            cmd = f"sudo mount -o remount,user_subvol_rm_allowed {dst_vol.path}"
            _print_cmd(
                console,
                wrap_cmd(cmd, dst_vol, resolved_endpoints),
            )
            console.print(
                f"{p2}To persist, add"
                " user_subvol_rm_allowed to"
                " the mount options in /etc/fstab"
                f" for {dst_vol.path}."
            )
        case SyncError.DST_EP_STAGING_SUBVOL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/{STAGING_DIR}",
                f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DST_EP_SNAPSHOTS_DIR_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            if dst_ep.hard_link_snapshots.enabled:
                cmds = [f"mkdir -p {path}/{SNAPSHOTS_DIR}"]
            else:
                cmds = [
                    f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DST_EP_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination endpoint directory"
                f" {path}/ is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.DST_EP_SNAPSHOTS_DIR_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {SNAPSHOTS_DIR}/"
                f" directory ({path}/{SNAPSHOTS_DIR})"
                " is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.DST_EP_STAGING_SUBVOL_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {STAGING_DIR}/"
                f" directory ({path}/{STAGING_DIR})"
                " is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.DST_EP_LATEST_SYMLINK_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.DST_EP_LATEST_SYMLINK_INVALID:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Reset it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case SyncError.DST_VOL_NO_HARDLINK_SUPPORT:
            console.print(
                f"{p2}The destination filesystem does not"
                " support hard links (e.g. FAT/exFAT)."
                " Use a filesystem like ext4, xfs, or"
                " btrfs, or use btrfs-snapshots instead."
            )
        case SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING:
            console.print(
                f"{p2}The source endpoint's latest symlink"
                " points to /dev/null (no snapshot yet)."
                " In dry-run mode, the upstream sync does"
                " not create a real snapshot, so this sync"
                " is skipped. Run without --dry-run to"
                " execute the full chain."
            )


def _print_mount_error(
    console: Console,
    title: str,
    details: str,
) -> None:
    """Print a mount-related error with indented details."""
    p2 = _INDENT * 2
    console.print(f"{p2}{title}")
    for line in details.splitlines():
        console.print(f"{p2}{_INDENT}{line}")


def _print_device_not_present_fix(
    console: Console,
    mount: MountConfig | None,
) -> None:
    """Print fix for device not plugged in."""
    p2 = _INDENT * 2
    uuid = mount.device_uuid if mount else "<uuid>"
    console.print(f"{p2}Plug in the drive and verify:")
    _print_cmd(console, f"ls -la /dev/disk/by-uuid/{uuid}")
    console.print(f"{p2}Or with systemd:")
    _print_cmd(console, f"udevadm info /dev/disk/by-uuid/{uuid}")


def _print_mount_unit_not_configured_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for mount unit not configured in systemd."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    console.print(f"{p2}Mount unit not configured in systemd on {host}.")
    console.print(
        f"{p2}The volume path must have a corresponding"
        " fstab entry or native .mount unit."
    )
    if mount and mount.encryption:
        console.print(f"{p2}fstab example (encrypted):")
        _print_cmd(
            console,
            f"/dev/mapper/{mount.encryption.mapper_name}"
            f"  {vol.path}  btrfs  defaults,noauto  0  0",
            indent=3,
        )
    else:
        uuid = mount.device_uuid if mount else "<uuid>"
        console.print(f"{p2}fstab example (unencrypted):")
        _print_cmd(
            console,
            f"UUID={uuid}  {vol.path}  ext4  defaults,noauto  0  0",
            indent=3,
        )
    console.print(f"{p2}After editing fstab:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_mount_unit_mismatch_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    caps: VolumeCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for mount unit config mismatch."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mc = caps.mount if caps else None
    mount_unit = mc.mount_unit if mc else "<mount-unit>"
    actual_what = mc.mount_unit_what if mc else "?"
    actual_where = mc.mount_unit_where if mc else "?"
    if mount and mount.encryption:
        expected_what = f"/dev/mapper/{mount.encryption.mapper_name}"
    elif mount:
        expected_what = f"/dev/disk/by-uuid/{mount.device_uuid}"
    else:
        expected_what = "?"
    console.print(f"{p2}Mount unit config does not match on {host}.")
    console.print(f"{p2}Expected:")
    console.print(f"{p2}{_INDENT}Where={vol.path}")
    console.print(f"{p2}{_INDENT}What={expected_what}")
    console.print(f"{p2}Actual:")
    console.print(f"{p2}{_INDENT}Where={actual_where}")
    console.print(f"{p2}{_INDENT}What={actual_what}")
    console.print(f"{p2}Check with:")
    _print_cmd(
        console,
        f"systemctl show {mount_unit} -p What -p Where --no-pager",
        indent=3,
    )
    console.print(f"{p2}Fix fstab or .mount unit, then:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_cryptsetup_service_not_configured_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for cryptsetup service not configured."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mapper = mount.encryption.mapper_name if mount and mount.encryption else "<mapper>"
    uuid = mount.device_uuid if mount else "<uuid>"
    console.print(
        f"{p2}Cryptsetup service"
        f" systemd-cryptsetup@{mapper}.service"
        f" not configured in systemd on {host}."
    )
    console.print(
        f"{p2}The encrypted volume must have a"
        " corresponding crypttab entry or native"
        " service unit."
    )
    console.print(f"{p2}crypttab example:")
    _print_cmd(
        console,
        f"{mapper}  UUID={uuid}  none  luks,noauto",
        indent=3,
    )
    console.print(f"{p2}After editing crypttab:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_cryptsetup_service_mismatch_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    caps: VolumeCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for cryptsetup service config mismatch."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mapper = mount.encryption.mapper_name if mount and mount.encryption else "<mapper>"
    uuid = mount.device_uuid if mount else "<uuid>"
    mc = caps.mount if caps else None
    actual_exec = mc.cryptsetup_service_exec_start if mc else "?"
    service = f"systemd-cryptsetup@{mapper}.service"
    console.print(f"{p2}Cryptsetup service config does not match on {host}.")
    console.print(f"{p2}Expected ExecStart to contain mapper={mapper} and UUID={uuid}.")
    console.print(f"{p2}Actual ExecStart: {actual_exec}")
    console.print(f"{p2}Check with:")
    _print_cmd(
        console,
        f"systemctl show {service} -p ExecStart --no-pager",
        indent=3,
    )
    console.print(f"{p2}Fix crypttab or service unit, then:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_passphrase_not_available_fix(
    console: Console,
    mount: MountConfig | None,
) -> None:
    """Print fix for passphrase not available."""
    p2 = _INDENT * 2
    pid = (
        mount.encryption.passphrase_id
        if mount and mount.encryption
        else "<passphrase-id>"
    )
    env_var = f"NBKP_PASSPHRASE_{pid.upper().replace('-', '_')}"
    console.print(f"{p2}Configure with your credential provider:")
    console.print(f"{p2}{_INDENT}keyring: keyring set nbkp {pid}")
    console.print(f"{p2}{_INDENT}env: export {env_var}=...")
    console.print(f"{p2}{_INDENT}command: ensure <credential-command> works")


def _print_mount_failed_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig | None,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for attach-luks/mount failure."""
    p2 = _INDENT * 2
    if mount and mount.encryption:
        mapper = mount.encryption.mapper_name
        uuid = mount.device_uuid
        console.print(f"{p2}Manual steps:")
        _print_cmd(
            console,
            wrap_cmd(
                f"sudo systemd-cryptsetup attach {mapper}"
                f" /dev/disk/by-uuid/{uuid} /dev/stdin luks",
                vol,
                resolved_endpoints,
            ),
            indent=3,
        )
        console.print(f"{p2}Check journal:")
        _print_cmd(
            console,
            f"journalctl -u systemd-cryptsetup@{mapper}.service",
            indent=3,
        )
    else:
        console.print(f"{p2}Check journal for mount errors.")


def _resolve_volume_user(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Resolve the system user for auth rules on a volume's host.

    For remote volumes, uses the SSH endpoint user. For local volumes
    or when the SSH user is unset, falls back to the current OS user.
    """
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints.get(vol.slug)
            if ep and ep.server.user:
                return ep.server.user
            else:
                return getpass.getuser()
        case LocalVolume():
            return getpass.getuser()


def _print_polkit_rules_missing_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for missing polkit rules, including generated content."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    user = _resolve_volume_user(vol, resolved_endpoints)
    rules = generate_auth_rules(config, user)
    console.print(f"{p2}polkit rules not configured on {host}.")
    console.print(f"{p2}Required for systemctl start/stop authorization without sudo.")
    if rules.polkit:
        console.print(f"{p2}Install to: {POLKIT_RULES_PATH}")
        _print_cmd(console, rules.polkit.rstrip(), indent=3)
    console.print(f"{p2}Or generate with: nbkp config setup-auth -c <config>")


def _print_sudoers_rules_missing_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for missing sudoers rules, including generated content."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    user = _resolve_volume_user(vol, resolved_endpoints)
    rules = generate_auth_rules(config, user)
    console.print(f"{p2}sudoers rules not configured on {host}.")
    console.print(f"{p2}Required for passwordless sudo systemd-cryptsetup attach.")
    if rules.sudoers:
        console.print(f"{p2}Install with: sudo visudo -f {SUDOERS_RULES_PATH}")
        _print_cmd(console, rules.sudoers.rstrip(), indent=3)
    console.print(f"{p2}Or generate with: nbkp config setup-auth -c <config>")


def print_human_troubleshoot(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> None:
    """Print troubleshooting instructions."""
    re = resolved_endpoints or {}
    if console is None:
        console = Console()

    failed_vols = [vs for vs in vol_statuses.values() if vs.errors]
    failed_syncs = [ss for ss in sync_statuses.values() if ss.errors]

    for vs in failed_vols:
        console.print(f"\n[bold]Volume {vs.slug!r}:[/bold]")
        vol = vs.config
        for error in vs.errors:
            console.print(f"{_INDENT}{error.value}")
            match error:
                case VolumeError.SENTINEL_NOT_FOUND:
                    _print_sentinel_fix(
                        console,
                        vol,
                        vol.path,
                        VOLUME_SENTINEL,
                        re,
                    )
                case VolumeError.UNREACHABLE:
                    match vol:
                        case RemoteVolume():
                            ep = re[vol.slug]
                            _print_ssh_troubleshoot(
                                console,
                                ep.server,
                                ep.proxy_chain,
                            )
                case VolumeError.LOCATION_EXCLUDED:
                    p2 = _INDENT * 2
                    console.print(
                        f"{p2}All SSH endpoints for this"
                        " volume are at an excluded"
                        " location. Remove"
                        " --exclude-location or add an"
                        " endpoint at a different"
                        " location."
                    )
                case VolumeError.VOLUME_NOT_MOUNTED:
                    _print_mount_error(
                        console,
                        "Volume is not mounted.",
                        f"Mount the volume with: nbkp volumes mount -n {vs.slug}",
                    )
                case VolumeError.DEVICE_NOT_PRESENT:
                    mount = vol.mount
                    _print_device_not_present_fix(console, mount)
                case VolumeError.SYSTEMCTL_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"systemctl not found on {host}.",
                        "Mount management requires systemd."
                        " Install systemd or disable mount"
                        " config for this volume.",
                    )
                case VolumeError.SYSTEMD_ESCAPE_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"systemd-escape not found on {host}.",
                        "Install: sudo apt install systemd"
                        " (usually pre-installed)\n"
                        "Check: which systemd-escape",
                    )
                case VolumeError.MOUNT_UNIT_NOT_CONFIGURED:
                    _print_mount_unit_not_configured_fix(
                        console,
                        vol,
                        re,
                    )
                case VolumeError.MOUNT_UNIT_MISMATCH:
                    _print_mount_unit_mismatch_fix(
                        console,
                        vol,
                        vs.diagnostics.capabilities,
                        re,
                    )
                case VolumeError.SUDO_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"sudo not found on {host}.",
                        "sudo is required for mount"
                        " operations (cryptsetup, mount/umount).\n"
                        "Install: apt install sudo\n"
                        "Check: which sudo",
                    )
                case VolumeError.CRYPTSETUP_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"cryptsetup not found on {host}.",
                        "Install: sudo apt install cryptsetup\nCheck: which cryptsetup",
                    )
                case VolumeError.SYSTEMD_CRYPTSETUP_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"systemd-cryptsetup not found on {host}.",
                        "This binary is part of systemd but"
                        " requires the cryptsetup package"
                        " (libcryptsetup).\n"
                        "Install: sudo apt install cryptsetup\n"
                        "Check: ls /usr/lib/systemd/"
                        "systemd-cryptsetup",
                    )
                case VolumeError.MOUNT_CMD_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"mount command not found on {host}.",
                        "Install: sudo apt install util-linux\nCheck: which mount",
                    )
                case VolumeError.UMOUNT_CMD_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"umount command not found on {host}.",
                        "Install: sudo apt install util-linux\nCheck: which umount",
                    )
                case VolumeError.MOUNTPOINT_CMD_NOT_FOUND:
                    host = host_label(vol, re)
                    _print_mount_error(
                        console,
                        f"mountpoint command not found on {host}.",
                        "Install: sudo apt install util-linux\nCheck: which mountpoint",
                    )
                case VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED:
                    _print_cryptsetup_service_not_configured_fix(
                        console,
                        vol,
                        re,
                    )
                case VolumeError.CRYPTSETUP_SERVICE_MISMATCH:
                    _print_cryptsetup_service_mismatch_fix(
                        console,
                        vol,
                        vs.diagnostics.capabilities,
                        re,
                    )
                case VolumeError.POLKIT_RULES_MISSING:
                    _print_polkit_rules_missing_fix(
                        console,
                        vol,
                        config,
                        re,
                    )
                case VolumeError.SUDOERS_RULES_MISSING:
                    _print_sudoers_rules_missing_fix(
                        console,
                        vol,
                        config,
                        re,
                    )
                case VolumeError.PASSPHRASE_NOT_AVAILABLE:
                    mount = vol.mount
                    _print_passphrase_not_available_fix(console, mount)
                case VolumeError.ATTACH_LUKS_FAILED | VolumeError.MOUNT_FAILED:
                    mount = vol.mount
                    _print_mount_failed_fix(console, vol, mount, re)

    for ss in failed_syncs:
        console.print(f"\n[bold]Sync {ss.slug!r}:[/bold]")
        for sync_error in ss.errors:
            console.print(f"{_INDENT}{sync_error.value}")
            _print_sync_error_fix(
                console,
                ss.config,
                sync_error,
                config,
                re,
            )

    if not failed_vols and not failed_syncs:
        console.print("No issues found. All volumes and syncs are active.")
