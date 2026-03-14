"""Preflight check output formatting."""

from __future__ import annotations

import shlex

from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
)
from ..config.output import (
    _sync_endpoint_display,
    _sync_options,
    endpoint_path,
    format_volume_display,
    host_label,
)
from ..remote.ssh import (
    format_proxy_jump_chain,
    ssh_prefix,
    wrap_cmd,
)
from ..sync.rsync import build_rsync_command
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from .status import (
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
        return Text("active", style="green")
    else:
        error_str = ", ".join(r.value for r in errors)
        return Text(f"inactive ({error_str})", style="red")


# Sync errors whose diagnostic state is visible as ✓/✗ in the
# Src/Dst Diagnostics columns.  When *all* errors for a sync are
# diagnostic-visible, the Status column shows a plain "inactive"
# because the diagnostics already tell the story.  Non-obvious
# errors (disabled, unavailable, tool-version, dry-run) are always
# shown in the Status column so they aren't silently hidden.
_DIAGNOSTIC_VISIBLE_SYNC_ERRORS: frozenset[SyncError] = frozenset(
    {
        # Volume-level — shown as ✗volume(...) in src/dst diagnostics
        SyncError.SOURCE_UNAVAILABLE,
        SyncError.DESTINATION_UNAVAILABLE,
        # Endpoint-level — shown as ✓/✗ items in src/dst diagnostics
        SyncError.SOURCE_SENTINEL_NOT_FOUND,
        SyncError.SOURCE_LATEST_NOT_FOUND,
        SyncError.SOURCE_LATEST_INVALID,
        SyncError.SOURCE_SNAPSHOTS_DIR_NOT_FOUND,
        SyncError.DESTINATION_SENTINEL_NOT_FOUND,
        SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE,
        SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME,
        SyncError.DESTINATION_TMP_NOT_FOUND,
        SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND,
        SyncError.DESTINATION_LATEST_NOT_FOUND,
        SyncError.DESTINATION_LATEST_INVALID,
        SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE,
        SyncError.DESTINATION_STAGING_DIR_NOT_WRITABLE,
        SyncError.DESTINATION_NO_HARDLINK_SUPPORT,
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
        return Text("active", style="green")
    non_obvious = [e for e in errors if e not in _DIAGNOSTIC_VISIBLE_SYNC_ERRORS]
    if non_obvious:
        reason = ", ".join(e.value for e in non_obvious)
        return Text(f"inactive ({reason})", style="red")
    else:
        return Text("inactive", style="red")


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
        ]
        if flag
    ]
    return ", ".join(items) if items else "none"


def _check(ok: bool, label: str) -> str:
    """Format a diagnostic item as ✓label or ✗label."""
    return f"\u2713{label}" if ok else f"\u2717{label}"


def _format_volume_issues(vol_status: VolumeStatus) -> str:
    """Format volume-level issues as ✗volume(reason)."""
    reason = ", ".join(e.value for e in vol_status.errors)
    return f"\u2717volume ({reason})" if reason else "\u2717volume"


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
                f"\u2713latest \u2192 {diag.latest.raw_target}"
                if diag.latest.exists and diag.latest.raw_target
                else "\u2717latest"
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
                _check(diag.btrfs.is_subvolume, "subvolume"),
                _check(diag.btrfs.staging_dir_exists, "staging/"),
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
                f"\u2713latest \u2192 {diag.latest.raw_target}"
                if diag.latest.exists and diag.latest.raw_target
                else "\u2717latest"
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
    table.add_column("Capabilities")
    table.add_column("Status")

    for vs in vol_statuses.values():
        vol = vs.config
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
            _format_capabilities(vs.diagnostics.capabilities),
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


def _build_rsync_commands_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build the Rsync Commands table section."""
    active_syncs = [ss for ss in sync_statuses.values() if ss.active]
    if not active_syncs:
        return []
    table = Table(title="Rsync Commands:")
    table.add_column("Sync", style="bold")
    table.add_column("Command")

    for ss in active_syncs:
        dst_ep = config.destination_endpoint(ss.config)
        dest_suffix: str | None = None
        link_dest: str | None = None
        match dst_ep.snapshot_mode:
            case "btrfs":
                dest_suffix = STAGING_DIR
            case "hard-link":
                dest_suffix = f"{SNAPSHOTS_DIR}/<timestamp>"
                if ss.destination_latest_snapshot:
                    link_dest = f"../{ss.destination_latest_snapshot.name}"
        cmd = build_rsync_command(
            ss.config,
            config,
            resolved_endpoints=resolved_endpoints,
            dest_suffix=dest_suffix,
            link_dest=link_dest,
        )
        table.add_row(ss.slug, shlex.join(cmd))

    return [Text(""), table]


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
    text = Text()
    for i, warning in enumerate(warnings):
        if i > 0:
            text.append("\n")
        text.append("warning: ", style="yellow bold")
        text.append(warning)
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
        *_build_rsync_commands_section(sync_statuses, config, resolved_endpoints),
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

_RSYNC_INSTALL = (
    "Ubuntu/Debian: sudo apt install rsync\n"
    "Fedora/RHEL:   sudo dnf install rsync\n"
    "macOS:         brew install rsync"
)

_BTRFS_INSTALL = (
    "Ubuntu/Debian: sudo apt install btrfs-progs\n"
    "Fedora/RHEL:   sudo dnf install btrfs-progs"
)

_COREUTILS_INSTALL = (
    "Ubuntu/Debian: sudo apt install coreutils\n"
    "Fedora/RHEL:   sudo dnf install coreutils"
)

_UTIL_LINUX_INSTALL = (
    "Ubuntu/Debian: sudo apt install util-linux\n"
    "Fedora/RHEL:   sudo dnf install util-linux"
)


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
        case SyncError.SOURCE_UNAVAILABLE:
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
        case SyncError.DESTINATION_UNAVAILABLE:
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
        case SyncError.SOURCE_SENTINEL_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            _print_sentinel_fix(
                console,
                src_vol,
                path,
                SOURCE_SENTINEL,
                resolved_endpoints,
            )
        case SyncError.SOURCE_LATEST_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            cmds = [
                f"ln -sfn /dev/null {path}/{LATEST_LINK}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncError.SOURCE_LATEST_INVALID:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Ensure the upstream"
                " sync has run at least once,"
                " or reset it:"
            )
            cmds = [
                f"ln -sfn /dev/null {path}/{LATEST_LINK}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncError.SOURCE_SNAPSHOTS_DIR_NOT_FOUND:
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
        case SyncError.DESTINATION_SENTINEL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            _print_sentinel_fix(
                console,
                dst_vol,
                path,
                DESTINATION_SENTINEL,
                resolved_endpoints,
            )
        case SyncError.SOURCE_RSYNC_NOT_FOUND:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DESTINATION_RSYNC_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.SOURCE_RSYNC_TOO_OLD:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DESTINATION_RSYNC_TOO_OLD:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncError.DESTINATION_BTRFS_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install btrfs-progs on {host}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SyncError.DESTINATION_STAT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install coreutils (stat) on {host}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SyncError.DESTINATION_FINDMNT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install util-linux (findmnt) on {host}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SyncError.DESTINATION_NOT_BTRFS:
            console.print(f"{p2}The destination is not on a btrfs filesystem.")
        case SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME:
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
        case SyncError.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM:
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
        case SyncError.DESTINATION_TMP_NOT_FOUND:
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
        case SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND:
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
        case SyncError.DESTINATION_ENDPOINT_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination endpoint directory"
                f" {path}/ is not writable. Fix permissions:"
            )
            cmds = [
                f"sudo chown <user>:<group> {path}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {SNAPSHOTS_DIR}/"
                f" directory ({path}/{SNAPSHOTS_DIR})"
                " is not writable. Fix permissions:"
            )
            cmds = [
                f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DESTINATION_STAGING_DIR_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {STAGING_DIR}/"
                f" directory ({path}/{STAGING_DIR})"
                " is not writable. Fix permissions:"
            )
            cmds = [
                f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DESTINATION_LATEST_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            cmds = [
                f"ln -sfn /dev/null {path}/{LATEST_LINK}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DESTINATION_LATEST_INVALID:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Reset it:"
            )
            cmds = [
                f"ln -sfn /dev/null {path}/{LATEST_LINK}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncError.DESTINATION_NO_HARDLINK_SUPPORT:
            console.print(
                f"{p2}The destination filesystem does not"
                " support hard links (e.g. FAT/exFAT)."
                " Use a filesystem like ext4, xfs, or"
                " btrfs, or use btrfs-snapshots instead."
            )
        case SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING:
            console.print(
                f"{p2}The source endpoint's latest symlink"
                " points to /dev/null (no snapshot yet)."
                " In dry-run mode, the upstream sync does"
                " not create a real snapshot, so this sync"
                " is skipped. Run without --dry-run to"
                " execute the full chain."
            )


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
