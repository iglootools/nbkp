"""Config-level display formatting helpers."""

from __future__ import annotations

from pydantic import ValidationError
from pydantic_core import ErrorDetails
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import (
    Config,
    ConfigError,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SyncConfig,
    SyncEndpoint,
)


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
            else:
                host_part = (
                    f"{ep.server.user}@{ep.server.host}"
                    if ep.server.user
                    else ep.server.host
                )
                port_suffix = f":{ep.server.port}" if ep.server.port != 22 else ""
                return f"{host_part}{port_suffix}:{vol.path}"
        case LocalVolume():
            return vol.path


def host_label(
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


def endpoint_path(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None,
) -> str:
    """Resolve the full endpoint path."""
    if subdir:
        return f"{vol.path}/{subdir}"
    else:
        return vol.path


def _sync_endpoint_display(endpoint: SyncEndpoint) -> str:
    """Format a sync endpoint as volume or volume/subdir."""
    if endpoint.subdir:
        return f"{endpoint.volume}:/{endpoint.subdir}"
    else:
        return endpoint.volume


def _sync_options(sync: SyncConfig, config: Config) -> str:
    """Build a comma-separated summary of notable sync options.

    Shown:
    - rsync-filter: filters or filter_file configured on the sync
    - src-snapshots: source reads from latest/ instead of volume root
      (btrfs or hard-link)
    - dst-snapshots: destination snapshot mode with optional max count
      (btrfs or hard-link)

    Omitted (available via config show / JSON output):
    - rsync flags (compress, checksum, extra_options) — per-sync detail,
      not structural
    - filter rules — too verbose for a summary column
    - enabled/disabled — already in the Status column
    """
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    return ", ".join(
        opt
        for opt in [
            "rsync-filter" if sync.filters or sync.filter_file else "",
            f"src-snapshots: {src_ep.snapshot_mode}"
            if src_ep.snapshot_mode != "none"
            else "",
            _snapshot_label(
                "dst-snapshots: btrfs", dst_ep.btrfs_snapshots.max_snapshots
            )
            if dst_ep.btrfs_snapshots.enabled
            else "",
            _snapshot_label(
                "dst-snapshots: hard-link",
                dst_ep.hard_link_snapshots.max_snapshots,
            )
            if dst_ep.hard_link_snapshots.enabled
            else "",
        ]
        if opt
    )


def _snapshot_label(name: str, max_snapshots: int | None) -> str:
    """Format a snapshot backend label with optional max count."""
    return f"{name}(max:{max_snapshots})" if max_snapshots is not None else name


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


def _format_validation_error(err: ErrorDetails) -> str:
    """Format a single Pydantic validation error for display."""
    loc = " → ".join(str(p) for p in err["loc"])
    msg = str(err["msg"])
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, ") :]
    return f"{loc}: {msg}" if loc else msg


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
            body = "\n".join(_format_validation_error(err) for err in cause.errors())
        case _:
            body = str(e)
    title = f"Config error [{e.reason}]"
    console.print(Panel(body, title=title, style="red"))
