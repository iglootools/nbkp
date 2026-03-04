"""CLI output formatting."""

from __future__ import annotations

import enum
import shlex

from pydantic import ValidationError
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .config import (
    Config,
    ConfigError,
    DestinationSyncEndpoint,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from .sync import PruneResult, SyncResult
from .sync.rsync import build_rsync_command
from .check import SyncReason, SyncStatus, VolumeReason, VolumeStatus
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


def _sync_options(sync: SyncConfig) -> str:
    """Build a comma-separated string of enabled sync options."""
    opts: list[str] = []
    if sync.filters or sync.filter_file:
        opts.append("rsync-filter")
    if sync.source.snapshot_mode != "none":
        opts.append(f"src:{sync.source.snapshot_mode}")
    if sync.destination.btrfs_snapshots.enabled:
        btrfs_label = "btrfs-snapshots"
        max_snap = sync.destination.btrfs_snapshots.max_snapshots
        if max_snap is not None:
            btrfs_label += f"(max:{max_snap})"
        opts.append(btrfs_label)
    if sync.destination.hard_link_snapshots.enabled:
        hl_label = "hard-link-snapshots"
        max_snap = sync.destination.hard_link_snapshots.max_snapshots
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
            ep = resolved_endpoints[vol.slug]
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
            _sync_endpoint_display(ss.config.source),
            _sync_endpoint_display(ss.config.destination),
            _sync_options(ss.config),
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
            cmd = build_rsync_command(
                ss.config,
                config,
                resolved_endpoints=resolved_endpoints,
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
                title="[bold]Check Results[/bold]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    else:
        for section in sections:
            console.print(section)


def print_human_results(
    results: list[SyncResult],
    dry_run: bool,
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
        if r.success:
            status = Text("OK", style="green")
        else:
            status = Text("FAILED", style="red")

        details_parts: list[str] = []
        if r.error:
            details_parts.append(f"Error: {r.error}")
        if r.snapshot_path:
            details_parts.append(f"Snapshot: {r.snapshot_path}")
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
        if r.error:
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
            f"ssh-copy-id {proxy_opt}{port_flag}"
            f"-i {server.key} {user_host}",
            indent=4,
        )
    else:
        console.print(f"{p3}1. Generate a key:")
        _print_cmd(console, "ssh-keygen -t ed25519", indent=4)
        console.print(f"{p3}2. Copy it to the server:")
        _print_cmd(
            console,
            f"ssh-copy-id {proxy_opt}{port_flag}" f"{user_host}",
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
    match reason:
        case SyncReason.DISABLED:
            console.print(f"{p2}Enable the sync in the" " configuration file.")
        case SyncReason.SOURCE_UNAVAILABLE:
            src = config.volumes[sync.source.volume]
            match src:
                case RemoteVolume():
                    ep = resolved_endpoints[src.slug]
                    _print_ssh_troubleshoot(
                        console,
                        ep.server,
                        ep.proxy_chain,
                    )
                case LocalVolume():
                    console.print(
                        f"{p2}Source volume"
                        f" '{sync.source.volume}'"
                        " is not available."
                    )
        case SyncReason.DESTINATION_UNAVAILABLE:
            dst = config.volumes[sync.destination.volume]
            match dst:
                case RemoteVolume():
                    ep = resolved_endpoints[dst.slug]
                    _print_ssh_troubleshoot(
                        console,
                        ep.server,
                        ep.proxy_chain,
                    )
                case LocalVolume():
                    console.print(
                        f"{p2}Destination volume"
                        f" '{sync.destination.volume}'"
                        " is not available."
                    )
        case SyncReason.SOURCE_SENTINEL_NOT_FOUND:
            src = config.volumes[sync.source.volume]
            path = _endpoint_path(src, sync.source.subdir)
            _print_sentinel_fix(
                console,
                src,
                path,
                ".nbkp-src",
                resolved_endpoints,
            )
        case SyncReason.SOURCE_LATEST_NOT_FOUND:
            src = config.volumes[sync.source.volume]
            path = _endpoint_path(src, sync.source.subdir)
            console.print(
                f"{p2}Source has snapshots enabled"
                f" but {path}/latest does not"
                " exist. Ensure the upstream"
                " sync has run at least once,"
                " or create it manually:"
            )
            if sync.source.btrfs_snapshots.enabled:
                cmds = [
                    "sudo btrfs subvolume create" f" {path}/latest",
                    "sudo chown <user>:<group>" f" {path}/latest",
                ]
            else:
                cmds = [
                    f"mkdir -p {path}/snapshots/initial",
                    f"ln -sfn snapshots/initial" f" {path}/latest",
                ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, src, resolved_endpoints),
                )
        case SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND:
            src = config.volumes[sync.source.volume]
            path = _endpoint_path(src, sync.source.subdir)
            if sync.source.btrfs_snapshots.enabled:
                cmds = [
                    f"sudo mkdir {path}/snapshots",
                    "sudo chown <user>:<group>" f" {path}/snapshots",
                ]
            else:
                cmds = [f"mkdir -p {path}/snapshots"]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, src, resolved_endpoints),
                )
        case SyncReason.DESTINATION_SENTINEL_NOT_FOUND:
            dst = config.volumes[sync.destination.volume]
            path = _endpoint_path(dst, sync.destination.subdir)
            _print_sentinel_fix(
                console,
                dst,
                path,
                ".nbkp-dst",
                resolved_endpoints,
            )
        case SyncReason.RSYNC_NOT_FOUND_ON_SOURCE:
            src = config.volumes[sync.source.volume]
            host = _host_label(src, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION:
            dst = config.volumes[sync.destination.volume]
            host = _host_label(dst, resolved_endpoints)
            console.print(f"{p2}Install rsync on {host}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_TOO_OLD_ON_SOURCE:
            src = config.volumes[sync.source.volume]
            host = _host_label(src, resolved_endpoints)
            console.print(
                f"{p2}rsync 3.0+ is required on {host}." " Install or upgrade:"
            )
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.RSYNC_TOO_OLD_ON_DESTINATION:
            dst = config.volumes[sync.destination.volume]
            host = _host_label(dst, resolved_endpoints)
            console.print(
                f"{p2}rsync 3.0+ is required on {host}." " Install or upgrade:"
            )
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION:
            dst = config.volumes[sync.destination.volume]
            host = _host_label(dst, resolved_endpoints)
            console.print(f"{p2}Install btrfs-progs on {host}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SyncReason.STAT_NOT_FOUND_ON_DESTINATION:
            dst = config.volumes[sync.destination.volume]
            host = _host_label(dst, resolved_endpoints)
            console.print(f"{p2}Install coreutils (stat)" f" on {host}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION:
            dst = config.volumes[sync.destination.volume]
            host = _host_label(dst, resolved_endpoints)
            console.print(f"{p2}Install util-linux (findmnt)" f" on {host}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SyncReason.DESTINATION_NOT_BTRFS:
            console.print(
                f"{p2}The destination is not on" " a btrfs filesystem."
            )
        case SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME:
            dst = config.volumes[sync.destination.volume]
            path = _endpoint_path(dst, sync.destination.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/latest",
                f"sudo mkdir {path}/snapshots",
                "sudo chown <user>:<group>" f" {path}/latest {path}/snapshots",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, dst, resolved_endpoints),
                )
        case SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM:
            dst = config.volumes[sync.destination.volume]
            console.print(
                f"{p2}Remount the btrfs volume" " with user_subvol_rm_allowed:"
            )
            cmd = (
                "sudo mount -o"
                " remount,user_subvol_rm_allowed"
                f" {dst.path}"
            )
            _print_cmd(
                console,
                _wrap_cmd(cmd, dst, resolved_endpoints),
            )
            console.print(
                f"{p2}To persist, add"
                " user_subvol_rm_allowed to"
                " the mount options in /etc/fstab"
                f" for {dst.path}."
            )
        case SyncReason.DESTINATION_LATEST_NOT_FOUND:
            dst = config.volumes[sync.destination.volume]
            path = _endpoint_path(dst, sync.destination.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/latest",
                "sudo chown <user>:<group>" f" {path}/latest",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, dst, resolved_endpoints),
                )
        case SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND:
            dst = config.volumes[sync.destination.volume]
            path = _endpoint_path(dst, sync.destination.subdir)
            if sync.destination.hard_link_snapshots.enabled:
                cmds = [f"mkdir -p {path}/snapshots"]
            else:
                cmds = [
                    f"sudo mkdir {path}/snapshots",
                    "sudo chown <user>:<group>" f" {path}/snapshots",
                ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    _wrap_cmd(cmd, dst, resolved_endpoints),
                )
        case SyncReason.DESTINATION_NO_HARDLINK_SUPPORT:
            console.print(
                f"{p2}The destination filesystem does not"
                " support hard links (e.g. FAT/exFAT)."
                " Use a filesystem like ext4, xfs, or"
                " btrfs, or use btrfs-snapshots instead."
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
        console.print("No issues found." " All volumes and syncs are active.")


def _sync_endpoint_display(
    endpoint: SyncEndpoint | DestinationSyncEndpoint,
) -> str:
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
            Text("yes", style="green")
            if sync.enabled
            else Text("no", style="red")
        )
        sync_table.add_row(
            sync.slug,
            _sync_endpoint_display(sync.source),
            _sync_endpoint_display(sync.destination),
            _sync_options(sync),
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
    console.print(Panel(body, title="Config error", style="red"))
