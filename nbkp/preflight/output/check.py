"""Check output: status tables for SSH endpoints, volumes, and syncs."""

from __future__ import annotations

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ...clihelpers import (
    OK_SYMBOL,
    Severity,
    severity_style,
    severity_symbol,
)
from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
)
from ...config.epresolution import ResolvedEndpoints
from ...config.output import (
    _sync_endpoint_display,
    _sync_options,
    format_mount_summary,
    format_volume_display,
)
from ..severity import severity_for_errors
from ..status import (
    DestinationEndpointError,
    LatestSymlinkState,
    SourceEndpointError,
    SshEndpointStatus,
    SyncStatus,
    VolumeStatus,
)
from ..strictness import Strictness
from .formatting import (
    check,
    format_capabilities,
    format_mount_status,
    join_text,
    status_text,
)


def _format_latest(latest: LatestSymlinkState) -> Text:
    """Format latest symlink state as styled text.

    A missing or invalid ``latest`` is always fatal under the standard
    strictness modes (``LATEST_SYMLINK_NOT_FOUND`` is not in any
    ``INACTIVE_*_ERRORS``), so this stays as an error icon.
    """
    if latest.exists and latest.raw_target:
        return Text(
            f"{OK_SYMBOL}latest → {latest.raw_target}",
            style=severity_style(Severity.OK),
        )
    else:
        return Text(
            f"{severity_symbol(Severity.ERROR)}latest",
            style=severity_style(Severity.ERROR),
        )


def _format_volume_issues(vol_status: VolumeStatus, strictness: Strictness) -> Text:
    """Format volume-level issues as warning/error volume(reason).

    Icon severity reflects whether any of the volume's errors are
    fatal under *strictness*.
    """
    severity = severity_for_errors(vol_status.errors, strictness)
    if severity is Severity.OK:
        severity = Severity.ERROR
    reason = ", ".join(e.value for e in vol_status.errors)
    symbol = severity_symbol(severity)
    label = f"{symbol}volume ({reason})" if reason else f"{symbol}volume"
    return Text(label, style=severity_style(severity))


def _format_source_diagnostics(ss: SyncStatus, strictness: Strictness) -> Text:
    """Format source endpoint diagnostics as styled text.

    When the source volume is inactive, shows warning/error
    volume(reason) instead of endpoint-level items (which weren't
    computed).  Otherwise shows OK/warning/error for each checked
    item.  Items only appear when the corresponding feature is
    configured (e.g. snapshots/ and latest are omitted when the
    endpoint has no snapshot mode).
    """
    src_ep = ss.source_endpoint_status
    if not src_ep.volume_status.active:
        return _format_volume_issues(src_ep.volume_status, strictness)
    diag = src_ep.diagnostics
    if diag is None:
        return Text("")
    items = [
        check(
            diag.sentinel_exists,
            "sentinel",
            fail_error=SourceEndpointError.SENTINEL_NOT_FOUND,
            strictness=strictness,
        ),
        *(
            [
                check(
                    diag.snapshot_dirs.exists,
                    "snapshots/",
                    fail_error=SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
                    strictness=strictness,
                )
            ]
            if diag.snapshot_dirs is not None
            else []
        ),
        *([_format_latest(diag.latest)] if diag.latest is not None else []),
    ]
    return join_text(items)


def _format_destination_diagnostics(ss: SyncStatus, strictness: Strictness) -> Text:
    """Format destination endpoint diagnostics as styled text.

    When the destination volume is inactive, shows warning/error
    volume(reason) instead of endpoint-level items.  Otherwise shows
    OK/warning/error for each checked item.  Btrfs-specific items
    (subvolume, staging/) only appear when btrfs diagnostics are
    present.  Snapshot items only appear when snapshot mode is
    configured.
    """
    dst_ep = ss.destination_endpoint_status
    if not dst_ep.volume_status.active:
        return _format_volume_issues(dst_ep.volume_status, strictness)
    diag = dst_ep.diagnostics
    if diag is None:
        return Text("")
    items = [
        check(
            diag.sentinel_exists,
            "sentinel",
            fail_error=DestinationEndpointError.SENTINEL_NOT_FOUND,
            strictness=strictness,
        ),
        check(
            diag.endpoint_writable,
            "writable",
            fail_error=DestinationEndpointError.NOT_WRITABLE,
            strictness=strictness,
        ),
        *(
            [
                check(
                    diag.btrfs.staging_exists,
                    "staging/",
                    fail_error=DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND,
                    strictness=strictness,
                ),
                check(
                    diag.btrfs.staging_is_subvolume,
                    "staging-subvolume",
                    fail_error=DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME,
                    strictness=strictness,
                ),
            ]
            if diag.btrfs is not None
            else []
        ),
        *(
            [
                check(
                    diag.snapshot_dirs.exists,
                    "snapshots/",
                    fail_error=DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND,
                    strictness=strictness,
                )
            ]
            if diag.snapshot_dirs is not None
            else []
        ),
        *([_format_latest(diag.latest)] if diag.latest is not None else []),
    ]
    return join_text(items)


def _build_ssh_endpoints_section(
    config: Config,
    ssh_endpoint_statuses: dict[str, SshEndpointStatus],
    strictness: Strictness,
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
    table.add_column("Status")

    for server in config.ssh_endpoints.values():
        ssh_status = ssh_endpoint_statuses.get(server.slug)
        if ssh_status is not None:
            status = status_text(ssh_status.active, ssh_status.errors, strictness)
        else:
            status = Text("")
        table.add_row(
            server.slug,
            server.host,
            str(server.port),
            server.user or "",
            server.key or "",
            ", ".join(server.proxy_jump_chain) or "",
            ", ".join(server.location_list),
            status,
        )

    return [table, Text("")]


def _build_volumes_section(
    vol_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints,
    strictness: Strictness,
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
        caps = vs.diagnostics.capabilities if vs.diagnostics else None
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
            format_mount_status(mount_caps, vol.mount, strictness),
            format_capabilities(caps),
            status_text(vs.active, vs.errors, strictness),
        )

    return [table, Text("")]


def _build_syncs_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    strictness: Strictness,
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
            _format_source_diagnostics(ss, strictness),
            _format_destination_diagnostics(ss, strictness),
            status_text(ss.active, ss.errors, strictness),
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
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[RenderableType]:
    """Build renderable sections for check output."""
    return [
        *_build_ssh_endpoints_section(config, ssh_statuses, strictness),
        *_build_volumes_section(vol_statuses, resolved_endpoints, strictness),
        *_build_syncs_section(sync_statuses, config, strictness),
        *_build_orphan_warnings_section(config),
    ]


def print_human_check(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    wrap_in_panel: bool = True,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> None:
    """Print human-readable status output."""
    re = resolved_endpoints or {}
    if console is None:
        console = Console()

    sections = build_check_sections(
        ssh_statuses, vol_statuses, sync_statuses, config, re, strictness
    )

    has_errors = any(not s.active for s in sync_statuses.values())
    if has_errors:
        sections.append(Text(""))
        sections.append(
            Text.from_markup(
                "Run [bold]nbkp preflight troubleshoot[/bold] for detailed remediation steps."
            )
        )

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
