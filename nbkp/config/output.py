"""Config-level display formatting helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

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
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from .protocol.volume import MountConfig


class SelectedEndpoint(Protocol):
    """The SSH endpoint chosen for a remote volume at runtime.

    Structural so that config display does not depend on ``remote``, where
    endpoint selection (and its ``ResolvedEndpoint`` model) lives.
    """

    @property
    def server(self) -> SshEndpoint: ...


SelectedEndpoints = Mapping[str, SelectedEndpoint]
"""Selected endpoint per remote volume slug (``remote.endpoints.SelectedEndpoints``)."""


def format_volume_display(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: SelectedEndpoints,
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
            return _display_path(vol)


def _display_path(vol: LocalVolume | RemoteVolume) -> str:
    """Path for display: the declared path, or a marker for discovered ones."""
    return vol.path if vol.path is not None else "(discovered at runtime)"


def host_label(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: SelectedEndpoints,
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
    path = _display_path(vol)
    if subdir:
        return f"{path}/{subdir}"
    else:
        return path


def format_mount_summary(mount: MountConfig | None) -> str:
    """Compact mount config summary for table display."""
    if mount is None:
        return ""
    return " ".join(
        [
            f"uuid:{mount.device_uuid[:8]}\u2026",
            *(["luks"] if mount.encryption is not None else []),
        ]
    )


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
            f"src-snapshots:{src_ep.snapshot_mode}"
            if src_ep.snapshot_mode != "none"
            else "",
            _snapshot_label("dst-snapshots:btrfs", dst_ep.btrfs_snapshots.max_snapshots)
            if dst_ep.btrfs_snapshots.enabled
            else "",
            _snapshot_label(
                "dst-snapshots:hard-link",
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
    resolved_endpoints: SelectedEndpoints | None = None,
) -> None:
    """Print human-readable configuration."""
    re = resolved_endpoints or {}
    c = console or Console()
    tables = [
        *([_ssh_endpoints_table(config)] if config.ssh_endpoints else []),
        _volumes_table(config, re),
        _syncs_table(config),
    ]
    for i, table in enumerate(tables):
        if i > 0:
            c.print()
        c.print(table)


def _ssh_endpoints_table(config: Config) -> Table:
    table = Table(title="SSH Endpoints:")
    table.add_column("Name", style="bold")
    table.add_column("Host")
    table.add_column("Port")
    table.add_column("User")
    table.add_column("Key")
    table.add_column("Proxy Jump")
    table.add_column("Locations")
    for server in config.ssh_endpoints.values():
        # Host / user / key / paths are free-form config values; Text
        # keeps a bracket in one of them from being read as a style tag.
        table.add_row(
            server.slug,
            Text(server.host),
            str(server.port),
            Text(server.user or ""),
            Text(server.key or ""),
            ", ".join(server.proxy_jump_chain) or "",
            Text(", ".join(server.location_list)),
        )
    return table


def _volume_type_and_endpoint(
    vol: LocalVolume | RemoteVolume, re: SelectedEndpoints
) -> tuple[str, str]:
    match vol:
        case RemoteVolume():
            ep = re.get(vol.slug)
            return "remote", ep.server.slug if ep else vol.ssh_endpoint
        case LocalVolume():
            return "local", ""


def _volumes_table(config: Config, re: SelectedEndpoints) -> Table:
    table = Table(title="Volumes:")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("SSH Endpoint")
    table.add_column("URI")
    table.add_column("Mount Config")
    for vol in config.volumes.values():
        vol_type, ssh_ep = _volume_type_and_endpoint(vol, re)
        table.add_row(
            vol.slug,
            vol_type,
            ssh_ep,
            Text(format_volume_display(vol, re)),
            format_mount_summary(vol.mount),
        )
    return table


def _syncs_table(config: Config) -> Table:
    table = Table(title="Syncs:")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Destination")
    table.add_column("Options")
    table.add_column("Enabled")
    for sync in config.syncs.values():
        enabled = (
            Text("yes", style="green") if sync.enabled else Text("no", style="red")
        )
        table.add_row(
            sync.slug,
            Text(_sync_endpoint_display(config.source_endpoint(sync))),
            Text(_sync_endpoint_display(config.destination_endpoint(sync))),
            _sync_options(sync, config),
            enabled,
        )
    return table


def _validation_message(err: ErrorDetails) -> str:
    return str(err["msg"]).removeprefix("Value error, ")


def _format_validation_error(err: ErrorDetails) -> str:
    """Format a single Pydantic validation error for display."""
    loc = " → ".join(str(p) for p in err["loc"])
    msg = _validation_message(err)
    return f"{loc}: {msg}" if loc else msg


def _config_error_body(e: ConfigError) -> str:
    """The human-readable detail of a ConfigError, one problem per line."""
    match e.__cause__:
        case ValidationError() as cause:
            return "\n".join(_format_validation_error(err) for err in cause.errors())
        case _:
            return str(e)


def _validation_errors_json(cause: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "loc": [str(p) for p in err["loc"]],
            "type": err["type"],
            "message": _validation_message(err),
        }
        for err in cause.errors()
    ]


def config_error_json(e: ConfigError) -> dict[str, Any]:
    """A ConfigError as JSON-ready data for ``--output json``.

    Validation failures also list each problem with its location and its
    stable ``type`` code (see ``ConfigValidationCode``).
    """
    cause = e.__cause__
    return {
        "error": {
            "reason": e.reason.value,
            "message": _config_error_body(e),
            **(
                {"errors": _validation_errors_json(cause)}
                if isinstance(cause, ValidationError)
                else {}
            ),
        }
    }


def print_config_error(
    e: ConfigError,
    *,
    console: Console | None = None,
) -> None:
    """Print a ConfigError as a Rich panel to stderr."""
    if console is None:
        console = Console(stderr=True)
    body = _config_error_body(e)
    # Both wrapped in Text: the title's own brackets would be read as a style
    # tag (rendering a bare "Config error"), and the body carries YAML parser
    # and pydantic messages, which embed "[type=..., input_value=...]".
    title = Text(f"Config error [{e.reason}]")
    console.print(Panel(Text(body), title=title, style="red"))
