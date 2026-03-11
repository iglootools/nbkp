"""CLI output formatting."""

from __future__ import annotations

import enum
import shlex
from collections import defaultdict

from mermaid_ascii import parse_mermaid, render_ascii
from pydantic import ValidationError
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .config import (
    Config,
    ConfigError,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from .sync import PruneResult, SyncOutcome, SyncResult
from .sync.snapshots.btrfs import STAGING_DIR
from .sync.snapshots.common import LATEST_LINK, SNAPSHOTS_DIR
from .sync.rsync import build_rsync_command
from .preflight import SyncReason, SyncStatus, VolumeReason, VolumeStatus
from .remote.ssh import format_proxy_jump_chain


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"


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


def _sync_options(sync: SyncConfig, config: Config) -> str:
    """Build a comma-separated string of enabled sync options."""
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    opts: list[str] = []
    if sync.filters or sync.filter_file:
        opts.append("rsync-filter")
    if src_ep.snapshot_mode != "none":
        opts.append(f"src:{src_ep.snapshot_mode}")
    if dst_ep.btrfs_snapshots.enabled:
        btrfs_label = "btrfs-snapshots"
        max_snap = dst_ep.btrfs_snapshots.max_snapshots
        if max_snap is not None:
            btrfs_label += f"(max:{max_snap})"
        opts.append(btrfs_label)
    if dst_ep.hard_link_snapshots.enabled:
        hl_label = "hard-link-snapshots"
        max_snap = dst_ep.hard_link_snapshots.max_snapshots
        if max_snap is not None:
            hl_label += f"(max:{max_snap})"
        opts.append(hl_label)
    return ", ".join(opts)


def format_volume_display(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Format a volume for human display."""
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints.get(vol.slug)
            if ep is None:
                return f"{vol.ssh_endpoint}:{vol.path}"
            if ep.server.user:
                host_part = f"{ep.server.user}@{ep.server.host}"
            else:
                host_part = ep.server.host
            if ep.server.port != 22:
                host_part += f":{ep.server.port}"
            return f"{host_part}:{vol.path}"
        case LocalVolume():
            return vol.path


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


def _format_snapshot_display(
    snapshot_path: str,
    sync_slug: str,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Format a snapshot path with SSH URI prefix for remote volumes."""
    sync = config.syncs[sync_slug]
    dst_ep = config.destination_endpoint(sync)
    vol = config.volumes[dst_ep.volume]
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return f"{ep.server.host}:{snapshot_path}"
        case LocalVolume():
            return snapshot_path


def print_human_results(
    results: list[SyncResult],
    dry_run: bool,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable run results."""
    if console is None:
        console = Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"Sync results{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for r in results:
        match r.outcome:
            case SyncOutcome.SUCCESS:
                status = Text("OK", style="green")
            case SyncOutcome.CANCELLED:
                status = Text("CANCELLED", style="yellow")
            case SyncOutcome.SKIPPED:
                status = Text("SKIPPED", style="dim")
            case SyncOutcome.FAILED:
                status = Text("FAILED", style="red")

        details_parts: list[str] = []
        if r.detail:
            details_parts.append(f"Error: {r.detail}")
        if r.snapshot_path:
            display = _format_snapshot_display(
                r.snapshot_path,
                r.sync_slug,
                config,
                resolved_endpoints,
            )
            details_parts.append(f"Snapshot: {display}")
        if r.pruned_paths:
            details_parts.append(f"Pruned: {len(r.pruned_paths)} snapshot(s)")
        if r.output and not r.success:
            lines = r.output.strip().split("\n")[:5]
            details_parts.extend(lines)

        table.add_row(
            r.sync_slug,
            status,
            "\n".join(details_parts),
        )

    console.print(table)


def print_human_prune_results(
    results: list[PruneResult],
    dry_run: bool,
    *,
    console: Console | None = None,
) -> None:
    """Print human-readable prune results."""
    if console is None:
        console = Console()
    mode = " (dry run)" if dry_run else ""

    table = Table(
        title=f"NBKP prune{mode}:",
    )
    table.add_column("Name", style="bold")
    table.add_column("Deleted")
    table.add_column("Kept")
    table.add_column("Status")

    for r in results:
        if r.skipped:
            status = Text(f"SKIPPED ({r.detail})", style="dim")
        elif r.detail:
            status = Text("FAILED", style="red")
        else:
            status = Text("OK", style="green")

        table.add_row(
            r.sync_slug,
            str(len(r.deleted)),
            str(r.kept),
            status,
        )

    console.print(table)


def _ssh_prefix(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> str:
    """Build human-friendly SSH command prefix."""
    parts = ["ssh"]
    if server.port != 22:
        parts.extend(["-p", str(server.port)])
    if server.key:
        parts.extend(["-i", server.key])
    if proxy_chain:
        parts.extend(["-J", format_proxy_jump_chain(proxy_chain)])
    host = f"{server.user}@{server.host}" if server.user else server.host
    parts.append(host)
    return " ".join(parts)


def _wrap_cmd(
    cmd: str,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Wrap a shell command for remote execution."""
    match vol:
        case LocalVolume():
            return cmd
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            prefix = _ssh_prefix(ep.server, ep.proxy_chain)
            return f"{prefix} '{cmd}'"


def _endpoint_path(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None,
) -> str:
    """Resolve the full endpoint path."""
    if subdir:
        return f"{vol.path}/{subdir}"
    else:
        return vol.path


def _host_label(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Human-readable host label for a volume."""
    match vol:
        case LocalVolume():
            return "this machine"
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return ep.server.host


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
        _wrap_cmd(f"mkdir -p {path}", vol, resolved_endpoints),
    )
    _print_cmd(
        console,
        _wrap_cmd(
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
    ssh_cmd = _ssh_prefix(server, proxy_chain)
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
            path = _endpoint_path(src_vol, src_ep.subdir)
            _print_sentinel_fix(
                console,
                src_vol,
                path,
                ".nbkp-src",
                resolved_endpoints,
            )
        case SyncReason.SOURCE_LATEST_NOT_FOUND:
            path = _endpoint_path(src_vol, src_ep.subdir)
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
                    _wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncReason.SOURCE_LATEST_INVALID:
            path = _endpoint_path(src_vol, src_ep.subdir)
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
                    _wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND:
            path = _endpoint_path(src_vol, src_ep.subdir)
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
                    _wrap_cmd(cmd, src_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_SENTINEL_NOT_FOUND:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
            _print_sentinel_fix(
                console,
                dst_vol,
                path,
                ".nbkp-dst",
                resolved_endpoints,
            )
        case SyncReason.RSYNC_NOT_FOUND_ON_SOURCE:
            host = _host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION:
            host = _host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_TOO_OLD_ON_SOURCE:
            host = _host_label(src_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_TOO_OLD_ON_DESTINATION:
            host = _host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}rsync 3.0+ is required on {host}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION:
            host = _host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install btrfs-progs on {host}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SyncReason.STAT_NOT_FOUND_ON_DESTINATION:
            host = _host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install coreutils (stat) on {host}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION:
            host = _host_label(dst_vol, resolved_endpoints)
            console.print(f"{p2}Install util-linux (findmnt) on {host}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SyncReason.DESTINATION_NOT_BTRFS:
            console.print(f"{p2}The destination is not on a btrfs filesystem.")
        case SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM:
            console.print(f"{p2}Remount the btrfs volume with user_subvol_rm_allowed:")
            cmd = f"sudo mount -o remount,user_subvol_rm_allowed {dst_vol.path}"
            _print_cmd(
                console,
                _wrap_cmd(cmd, dst_vol, resolved_endpoints),
            )
            console.print(
                f"{p2}To persist, add"
                " user_subvol_rm_allowed to"
                " the mount options in /etc/fstab"
                f" for {dst_vol.path}."
            )
        case SyncReason.DESTINATION_TMP_NOT_FOUND:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/{STAGING_DIR}",
                f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_ENDPOINT_NOT_WRITABLE:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_WRITABLE:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_STAGING_DIR_NOT_WRITABLE:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_LATEST_NOT_FOUND:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case SyncReason.DESTINATION_LATEST_INVALID:
            path = _endpoint_path(dst_vol, dst_ep.subdir)
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
                    _wrap_cmd(cmd, dst_vol, resolved_endpoints),
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
                        ".nbkp-vol",
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


def _sync_endpoint_display(endpoint: SyncEndpoint) -> str:
    """Format a sync endpoint as volume or volume/subdir."""
    if endpoint.subdir:
        return f"{endpoint.volume}:/{endpoint.subdir}"
    else:
        return endpoint.volume


def print_human_config(
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> None:
    """Print human-readable configuration."""
    re = resolved_endpoints or {}
    if console is None:
        console = Console()

    if config.ssh_endpoints:
        server_table = Table(title="SSH Endpoints:")
        server_table.add_column("Name", style="bold")
        server_table.add_column("Host")
        server_table.add_column("Port")
        server_table.add_column("User")
        server_table.add_column("Key")
        server_table.add_column("Proxy Jump")
        server_table.add_column("Locations")

        for server in config.ssh_endpoints.values():
            server_table.add_row(
                server.slug,
                server.host,
                str(server.port),
                server.user or "",
                server.key or "",
                ", ".join(server.proxy_jump_chain) or "",
                ", ".join(server.location_list),
            )

        console.print(server_table)
        console.print()

    vol_table = Table(title="Volumes:")
    vol_table.add_column("Name", style="bold")
    vol_table.add_column("Type")
    vol_table.add_column("SSH Endpoint")
    vol_table.add_column("URI")

    for vol in config.volumes.values():
        match vol:
            case RemoteVolume():
                vol_type = "remote"
                ep = re.get(vol.slug)
                ssh_ep = ep.server.slug if ep else vol.ssh_endpoint
            case LocalVolume():
                vol_type = "local"
                ssh_ep = ""
        vol_table.add_row(
            vol.slug,
            vol_type,
            ssh_ep,
            format_volume_display(vol, re),
        )

    console.print(vol_table)
    console.print()

    sync_table = Table(title="Syncs:")
    sync_table.add_column("Name", style="bold")
    sync_table.add_column("Source")
    sync_table.add_column("Destination")
    sync_table.add_column("Options")
    sync_table.add_column("Enabled")

    for sync in config.syncs.values():
        enabled = (
            Text("yes", style="green") if sync.enabled else Text("no", style="red")
        )
        sync_table.add_row(
            sync.slug,
            _sync_endpoint_display(config.source_endpoint(sync)),
            _sync_endpoint_display(config.destination_endpoint(sync)),
            _sync_options(sync, config),
            enabled,
        )

    console.print(sync_table)


def print_config_error(
    e: ConfigError,
    *,
    console: Console | None = None,
) -> None:
    """Print a ConfigError as a Rich panel to stderr."""
    if console is None:
        console = Console(stderr=True)
    cause = e.__cause__
    match cause:
        case ValidationError():
            lines: list[str] = []
            for err in cause.errors():
                loc = " → ".join(str(p) for p in err["loc"])
                msg = err["msg"]
                if msg.startswith("Value error, "):
                    prefix_len = len("Value error, ")
                    msg = msg[prefix_len:]
                if loc:
                    lines.append(f"{loc}: {msg}")
                else:
                    lines.append(msg)
            body = "\n".join(lines)
        case _:
            body = str(e)
    title = f"Config error [{e.reason}]"
    console.print(Panel(body, title=title, style="red"))


# ── Graph rendering ──────────────────────────────────────────────────


def _endpoint_annotation(ep: SyncEndpoint) -> str:
    """Format snapshot mode annotation for a sync endpoint."""
    match ep.snapshot_mode:
        case "btrfs":
            max_s = ep.btrfs_snapshots.max_snapshots
            suffix = f", max: {max_s}" if max_s is not None else ""
            return f"btrfs{suffix}"
        case "hard-link":
            max_s = ep.hard_link_snapshots.max_snapshots
            suffix = f", max: {max_s}" if max_s is not None else ""
            return f"hard-link{suffix}"
        case _:
            return ""


def _build_graph_data(
    config: Config,
) -> tuple[
    dict[str, list[SyncConfig]],
    set[str],
]:
    """Build adjacency list and root set from config.

    Returns (children, roots) where:
    - children maps source endpoint slug → list of SyncConfig
    - roots is the set of endpoint slugs that are never destinations
    """
    children: dict[str, list[SyncConfig]] = defaultdict(list)
    all_sources: set[str] = set()
    all_destinations: set[str] = set()

    for sync in config.syncs.values():
        children[sync.source].append(sync)
        all_sources.add(sync.source)
        all_destinations.add(sync.destination)

    roots = all_sources - all_destinations
    return dict(children), roots


def build_mermaid_graph(config: Config) -> str:
    """Generate mermaid graph LR syntax from config."""
    children, roots = _build_graph_data(config)
    lines = ["graph LR"]
    visited: set[str] = set()

    def _walk(ep_slug: str) -> None:
        if ep_slug in visited:
            return
        visited.add(ep_slug)
        for sync in children.get(ep_slug, []):
            dst_slug = sync.destination
            lines.append(f"    {ep_slug} -->|{sync.slug}| {dst_slug}")
            _walk(dst_slug)

    for root in sorted(roots):
        _walk(root)

    # Include any endpoints not reachable from roots (cycles or isolated)
    for ep_slug in sorted(children.keys()):
        _walk(ep_slug)

    return "\n".join(lines)


def print_mermaid_ascii_graph(
    config: Config,
    *,
    console: Console | None = None,
) -> None:
    """Render the config graph as ASCII art using mermaid-ascii-diagrams."""
    if console is None:
        console = Console()
    mermaid_src = build_mermaid_graph(config)
    diagram = parse_mermaid(mermaid_src)
    console.print(render_ascii(diagram), highlight=False)


def print_rich_tree_graph(
    config: Config,
    *,
    console: Console | None = None,
) -> None:
    """Render the config graph as Rich Trees."""
    if console is None:
        console = Console()

    children, roots = _build_graph_data(config)

    def _add_children(tree: Tree, ep_slug: str, visited: set[str]) -> None:
        for sync in children.get(ep_slug, []):
            dst_slug = sync.destination
            dst_ep = config.sync_endpoints[dst_slug]
            annotation = _endpoint_annotation(dst_ep)
            label_parts = [f"[bold]{sync.slug}[/bold] -> {dst_slug}"]
            if annotation:
                label_parts.append(f"({annotation})")
            if not sync.enabled:
                label_parts.append("(disabled)")

            style = "dim" if not sync.enabled else ""
            label = " ".join(label_parts)
            child = tree.add(label, style=style)

            if dst_slug not in visited:
                visited.add(dst_slug)
                _add_children(child, dst_slug, visited)

    for root in sorted(roots):
        tree = Tree(f"[bold]{root}[/bold]")
        visited: set[str] = {root}
        _add_children(tree, root, visited)
        console.print(tree)


def print_mermaid_graph(config: Config) -> None:
    """Print raw mermaid graph syntax to stdout."""
    print(build_mermaid_graph(config))


def build_graph_json(config: Config) -> dict[str, object]:
    """Build JSON-serializable graph structure."""
    # Collect all endpoint slugs referenced by syncs
    ep_slugs: set[str] = set()
    for sync in config.syncs.values():
        ep_slugs.add(sync.source)
        ep_slugs.add(sync.destination)

    nodes = [
        {
            "slug": slug,
            "volume": ep.volume,
            "subdir": ep.subdir,
            "snapshot_mode": ep.snapshot_mode,
        }
        for slug in sorted(ep_slugs)
        if (ep := config.sync_endpoints.get(slug)) is not None
    ]

    edges = [
        {
            "sync": sync.slug,
            "source": sync.source,
            "destination": sync.destination,
            "enabled": sync.enabled,
        }
        for sync in config.syncs.values()
    ]

    return {"nodes": nodes, "edges": edges}
