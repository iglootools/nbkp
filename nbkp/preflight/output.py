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
from ..conventions import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from .status import SyncReason, SyncStatus, VolumeReason, VolumeStatus


def _status_text(
    active: bool,
    reasons: list[VolumeReason] | list[SyncReason],
) -> Text:
    """Format status with optional reasons as styled text."""
    if active:
        return Text("active", style="green")
    else:
        reason_str = ", ".join(r.value for r in reasons)
        return Text(f"inactive ({reason_str})", style="red")


def build_check_sections(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[RenderableType]:
    """Build renderable sections for check output."""
    sections: list[RenderableType] = []

    if config.ssh_endpoints:
        ep_table = Table(title="SSH Endpoints:")
        ep_table.add_column("Name", style="bold")
        ep_table.add_column("Host")
        ep_table.add_column("Port")
        ep_table.add_column("User")
        ep_table.add_column("Key")
        ep_table.add_column("Proxy Jump")
        ep_table.add_column("Locations")

        for server in config.ssh_endpoints.values():
            ep_table.add_row(
                server.slug,
                server.host,
                str(server.port),
                server.user or "",
                server.key or "",
                ", ".join(server.proxy_jump_chain) or "",
                ", ".join(server.location_list),
            )

        sections.append(ep_table)
        sections.append(Text(""))

    vol_table = Table(title="Volumes:")
    vol_table.add_column("Name", style="bold")
    vol_table.add_column("Type")
    vol_table.add_column("SSH Endpoint")
    vol_table.add_column("URI")
    vol_table.add_column("Status")

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
        vol_table.add_row(
            vs.slug,
            vol_type,
            ssh_ep,
            format_volume_display(vol, resolved_endpoints),
            _status_text(vs.active, vs.reasons),
        )

    sections.append(vol_table)
    sections.append(Text(""))

    sync_table = Table(title="Syncs:")
    sync_table.add_column("Name", style="bold")
    sync_table.add_column("Source")
    sync_table.add_column("Destination")
    sync_table.add_column("Options")
    sync_table.add_column("Status")

    for ss in sync_statuses.values():
        sync_table.add_row(
            ss.slug,
            _sync_endpoint_display(config.source_endpoint(ss.config)),
            _sync_endpoint_display(config.destination_endpoint(ss.config)),
            _sync_options(ss.config, config),
            _status_text(ss.active, ss.reasons),
        )

    sections.append(sync_table)

    active_syncs = [ss for ss in sync_statuses.values() if ss.active]
    if active_syncs:
        sections.append(Text(""))
        cmd_table = Table(title="Rsync Commands:")
        cmd_table.add_column("Sync", style="bold")
        cmd_table.add_column("Command")

        for ss in active_syncs:
            dst_ep = config.destination_endpoint(ss.config)
            dest_suffix: str | None = None
            link_dest: str | None = None
            match dst_ep.snapshot_mode:
                case "btrfs":
                    dest_suffix = STAGING_DIR
                case "hard-link":
                    dest_suffix = f"{SNAPSHOTS_DIR}/<timestamp>"
                    if ss.destination_latest_target:
                        link_dest = f"../{ss.destination_latest_target}"
            cmd = build_rsync_command(
                ss.config,
                config,
                resolved_endpoints=resolved_endpoints,
                dest_suffix=dest_suffix,
                link_dest=link_dest,
            )
            cmd_table.add_row(ss.slug, shlex.join(cmd))

        sections.append(cmd_table)

    return sections


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


def _print_sync_reason_fix(
    console: Console,
    sync: SyncConfig,
    reason: SyncReason,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix instructions for a sync reason."""
    p2 = _INDENT * 2
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    src_vol = config.volumes[src_ep.volume]
    dst_vol = config.volumes[dst_ep.volume]
    match reason:
        case SyncReason.DISABLED:
            console.print(f"{p2}Enable the sync in the configuration file.")
        case SyncReason.SOURCE_UNAVAILABLE:
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
        case SyncReason.DESTINATION_UNAVAILABLE:
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
        case SyncReason.SOURCE_SENTINEL_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            _print_sentinel_fix(
                console,
                src_vol,
                path,
                SOURCE_SENTINEL,
                resolved_endpoints,
            )
        case SyncReason.SOURCE_LATEST_NOT_FOUND:
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
        case SyncReason.SOURCE_LATEST_INVALID:
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
        case SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND:
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
        case SyncReason.DESTINATION_SENTINEL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            _print_sentinel_fix(
                console,
                dst_vol,
                path,
                DESTINATION_SENTINEL,
                resolved_endpoints,
            )
        case SyncReason.SOURCE_RSYNC_NOT_FOUND:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.DESTINATION_RSYNC_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.SOURCE_RSYNC_TOO_OLD:
            host = host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.DESTINATION_RSYNC_TOO_OLD:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.DESTINATION_BTRFS_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install btrfs-progs on {host}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SyncReason.DESTINATION_STAT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install coreutils (stat) on {host}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SyncReason.DESTINATION_FINDMNT_NOT_FOUND:
            host = host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install util-linux (findmnt) on {host}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SyncReason.DESTINATION_NOT_BTRFS:
            console.print(f"{p2}The destination is not on a btrfs filesystem.")
        case SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME:
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
        case SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM:
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
        case SyncReason.DESTINATION_TMP_NOT_FOUND:
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
        case SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND:
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
        case SyncReason.DESTINATION_ENDPOINT_NOT_WRITABLE:
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
        case SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE:
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
        case SyncReason.DESTINATION_STAGING_DIR_NOT_WRITABLE:
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
        case SyncReason.DESTINATION_LATEST_NOT_FOUND:
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
        case SyncReason.DESTINATION_LATEST_INVALID:
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
        case SyncReason.DESTINATION_NO_HARDLINK_SUPPORT:
            console.print(
                f"{p2}The destination filesystem does not"
                " support hard links (e.g. FAT/exFAT)."
                " Use a filesystem like ext4, xfs, or"
                " btrfs, or use btrfs-snapshots instead."
            )
        case SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING:
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
    has_issues = False

    failed_vols = [vs for vs in vol_statuses.values() if vs.reasons]
    failed_syncs = [ss for ss in sync_statuses.values() if ss.reasons]
    has_issues = bool(failed_vols or failed_syncs)

    for vs in failed_vols:
        console.print(f"\n[bold]Volume {vs.slug!r}:[/bold]")
        vol = vs.config
        for reason in vs.reasons:
            console.print(f"{_INDENT}{reason.value}")
            match reason:
                case VolumeReason.SENTINEL_NOT_FOUND:
                    _print_sentinel_fix(
                        console,
                        vol,
                        vol.path,
                        VOLUME_SENTINEL,
                        re,
                    )
                case VolumeReason.UNREACHABLE:
                    match vol:
                        case RemoteVolume():
                            ep = re[vol.slug]
                            _print_ssh_troubleshoot(
                                console,
                                ep.server,
                                ep.proxy_chain,
                            )
                case VolumeReason.LOCATION_EXCLUDED:
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
        for sync_reason in ss.reasons:
            console.print(f"{_INDENT}{sync_reason.value}")
            _print_sync_reason_fix(
                console,
                ss.config,
                sync_reason,
                config,
                re,
            )

    if not has_issues:
        console.print("No issues found. All volumes and syncs are active.")
