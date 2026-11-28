"""The human-readable report of ``nbkp preflight check``."""

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nbkp.clidisplay import severity_style, severity_symbol
from nbkp.config import Config, LocalVolume, RemoteVolume
from nbkp.config.output import (
    format_mount_summary,
    format_volume_display,
    sync_endpoint_display,
    sync_options_summary,
)
from nbkp.fsprotocol import DEVNULL_TARGET
from nbkp.policy import Severity, Strictness
from nbkp.preflight.output.commands import SuggestedCommands
from nbkp.preflight.output.formatting import (
    check,
    format_capabilities,
    format_mount_status,
    join_text,
    status_text,
)
from nbkp.preflight.output.messages import format_problem, format_problems
from nbkp.preflight.severity import severity_for_errors
from nbkp.preflight.status.endpoint import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    LatestSymlinkState,
    SourceEndpointErrorKind,
)
from nbkp.preflight.status.ssh import INACTIVE_SSH_ERRORS, SshEndpointStatus
from nbkp.preflight.status.sync import SyncStatus
from nbkp.preflight.status.volume import VolumeStatus
from nbkp.remote.endpoints import ResolvedEndpoints


def _format_latest(
    latest: LatestSymlinkState,
    *,
    not_found: SourceEndpointErrorKind | DestinationEndpointErrorKind,
    invalid: SourceEndpointErrorKind | DestinationEndpointErrorKind,
    strictness: Strictness,
) -> Text:
    """
    Format the ``latest`` symlink item.

    A missing ``latest`` is classified as *not_found*, one pointing at no snapshot as
    *invalid*, under *strictness*: an error under the standard modes, a warning under
    ``ignore-all``.
    """
    match latest:
        case LatestSymlinkState(exists=True, raw_target=str() as target) if (
            target == DEVNULL_TARGET or latest.target_valid is True
        ):
            return check(ok=True, label=f"latest → {target}")
        case LatestSymlinkState(exists=True, raw_target=str() as target):
            return check(
                ok=False,
                label=f"latest → {target}",
                fail_error=invalid,
                strictness=strictness,
            )
        case _:
            return check(
                ok=False,
                label="latest",
                fail_error=not_found,
                strictness=strictness,
            )


def _format_volume_issues(vol_status: VolumeStatus, strictness: Strictness) -> Text:
    severity = severity_for_errors(vol_status.errors, strictness)
    if severity is Severity.OK:
        severity = Severity.ERROR
    reason = format_problems(vol_status.errors)
    symbol = severity_symbol(severity)
    label = f"{symbol}volume ({reason})" if reason else f"{symbol}volume"
    return Text(label, style=severity_style(severity))


def _format_source_diagnostics(ss: SyncStatus, strictness: Strictness) -> Text:
    """
    Format the source column's diagnostics.

    Endpoint-level items are not computed on an inactive volume, so the volume's own
    issues are shown instead.
    """
    src_ep = ss.source_endpoint_status
    if not src_ep.volume_status.active:
        return _format_volume_issues(src_ep.volume_status, strictness)
    diag = src_ep.diagnostics
    if diag is None:
        return Text("")
    items = [
        check(
            ok=diag.sentinel_exists,
            label="sentinel",
            fail_error=SourceEndpointErrorKind.SENTINEL_NOT_FOUND,
            strictness=strictness,
        ),
        *(
            [
                check(
                    ok=diag.snapshot_dirs.exists,
                    label="snapshots/",
                    fail_error=SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND,
                    strictness=strictness,
                ),
            ]
            if diag.snapshot_dirs is not None
            else []
        ),
        *(
            [
                _format_latest(
                    diag.latest,
                    not_found=SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND,
                    invalid=SourceEndpointErrorKind.LATEST_SYMLINK_INVALID,
                    strictness=strictness,
                ),
            ]
            if diag.latest is not None
            else []
        ),
    ]
    return join_text(items)


def _format_destination_diagnostics(ss: SyncStatus, strictness: Strictness) -> Text:
    """
    Format the destination column's diagnostics.

    Endpoint-level items are not computed on an inactive volume, so the volume's own
    issues are shown instead.
    """
    dst_ep = ss.destination_endpoint_status
    if not dst_ep.volume_status.active:
        return _format_volume_issues(dst_ep.volume_status, strictness)
    diag = dst_ep.diagnostics
    if diag is None:
        return Text("")
    return join_text(
        [
            check(
                ok=diag.sentinel_exists,
                label="sentinel",
                fail_error=DestinationEndpointErrorKind.SENTINEL_NOT_FOUND,
                strictness=strictness,
            ),
            # Without the sentinel the directory may not exist, and the status
            # layer suppresses NOT_WRITABLE: so does this column.
            *(
                [
                    check(
                        ok=diag.endpoint_writable,
                        label="writable",
                        fail_error=DestinationEndpointErrorKind.NOT_WRITABLE,
                        strictness=strictness,
                    ),
                ]
                if diag.sentinel_exists
                else []
            ),
            *_destination_snapshot_items(diag, strictness),
            *(
                Text(
                    f"{severity_symbol(Severity.WARNING)}{format_problem(w)}",
                    style=severity_style(Severity.WARNING),
                )
                for w in dst_ep.warnings
            ),
        ],
    )


def _destination_snapshot_items(
    diag: DestinationEndpointDiagnostics,
    strictness: Strictness,
) -> list[Text]:
    return [
        *(
            [
                check(
                    ok=diag.btrfs.staging_exists,
                    label="staging/",
                    fail_error=DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND,
                    strictness=strictness,
                ),
                check(
                    ok=diag.btrfs.staging_is_subvolume,
                    label="staging-subvolume",
                    fail_error=DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME,
                    strictness=strictness,
                ),
            ]
            if diag.btrfs is not None
            else []
        ),
        *(
            [
                check(
                    ok=diag.snapshot_dirs.exists,
                    label="snapshots/",
                    fail_error=DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND,
                    strictness=strictness,
                ),
            ]
            if diag.snapshot_dirs is not None
            else []
        ),
        *(
            [
                _format_latest(
                    diag.latest,
                    not_found=DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND,
                    invalid=DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID,
                    strictness=strictness,
                ),
            ]
            if diag.latest is not None
            else []
        ),
    ]


def _ssh_status_text(status: SshEndpointStatus | None, strictness: Strictness) -> Text:
    match status:
        case None:
            return Text("")
        case SshEndpointStatus():
            return Text.assemble(
                status_text(
                    active=status.active,
                    errors=status.errors,
                    strictness=strictness,
                ),
                *(
                    (
                        f", {severity_symbol(Severity.WARNING)}{format_problem(w)}",
                        severity_style(Severity.WARNING),
                    )
                    for w in status.warnings
                ),
            )


def _localhost_row(
    status: SshEndpointStatus | None,
    strictness: Strictness,
) -> list[tuple[RenderableType, ...]]:
    """
    Return the implicit localhost endpoint's row, only when it has findings.

    Local volumes run their tools here (rsync, btrfs, udisks…), so its tool
    errors must be visible next to the configured endpoints.
    """
    return (
        [
            (
                "localhost",
                Text("this machine", style="dim"),
                "",
                "",
                "",
                "",
                "",
                _ssh_status_text(status, strictness),
            ),
        ]
        if status is not None and (status.errors or status.warnings)
        else []
    )


def _build_ssh_endpoints_section(
    config: Config,
    ssh_endpoint_statuses: dict[str, SshEndpointStatus],
    strictness: Strictness,
) -> list[RenderableType]:
    localhost = _localhost_row(ssh_endpoint_statuses.get("localhost"), strictness)
    if not config.ssh_endpoints and not localhost:
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

    for row in localhost:
        table.add_row(*row)
    for server in config.ssh_endpoints.values():
        # Host / user / key / paths are free-form config values; Text keeps a
        # bracket in one of them from being read as a style tag.
        table.add_row(
            server.slug,
            Text(server.host),
            str(server.port),
            Text(server.user or ""),
            Text(server.key or ""),
            ", ".join(server.proxy_jump_chain) or "",
            Text(", ".join(server.location_list)),
            _ssh_status_text(ssh_endpoint_statuses.get(server.slug), strictness),
        )

    return [table, Text("")]


def _build_volumes_section(
    vol_statuses: dict[str, VolumeStatus],
    resolved_endpoints: ResolvedEndpoints,
    strictness: Strictness,
) -> list[RenderableType]:
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
            Text(format_volume_display(vol, resolved_endpoints)),
            format_mount_summary(vol.mount),
            format_mount_status(mount_caps, vol.mount, strictness),
            format_capabilities(caps),
            status_text(active=vs.active, errors=vs.errors, strictness=strictness),
        )

    return [table, Text("")]


def _build_syncs_section(
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    strictness: Strictness,
) -> list[RenderableType]:
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
            Text(sync_endpoint_display(config.source_endpoint(ss.config))),
            Text(sync_endpoint_display(config.destination_endpoint(ss.config))),
            sync_options_summary(ss.config, config),
            _format_source_diagnostics(ss, strictness),
            _format_destination_diagnostics(ss, strictness),
            status_text(active=ss.active, errors=ss.errors, strictness=strictness),
        )

    return [table]


def _build_orphan_warnings_section(
    config: Config,
) -> list[RenderableType]:
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
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[RenderableType]:
    """
    Return the report's sections: SSH endpoints, volumes, syncs, then warnings.

    The warnings name the SSH endpoints, volumes and sync endpoints nothing uses.
    *strictness* decides which problems show as errors rather than as warnings.
    """
    return [
        *_build_ssh_endpoints_section(config, ssh_statuses, strictness),
        *_build_volumes_section(vol_statuses, resolved_endpoints, strictness),
        *_build_syncs_section(sync_statuses, config, strictness),
        *_build_orphan_warnings_section(config),
    ]


def format_human_check(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    wrap_in_panel: bool = True,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    invocation: SuggestedCommands,
) -> RenderableType:
    """
    Format the human-readable check report.

    *invocation* carries the config path and endpoint flags so the ``troubleshoot``
    suggestion can be copy-pasted as-is.
    """
    sections = Group(
        *build_check_sections(
            ssh_statuses,
            vol_statuses,
            sync_statuses,
            config,
            resolved_endpoints or {},
            strictness=strictness,
        ),
        *_troubleshoot_hint(ssh_statuses, sync_statuses, invocation),
    )
    return (
        Panel(
            sections,
            title="[bold]Preflight Checks[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
        if wrap_in_panel
        else sections
    )


def _troubleshoot_hint(
    ssh_statuses: dict[str, SshEndpointStatus],
    sync_statuses: dict[str, SyncStatus],
    invocation: SuggestedCommands,
) -> list[RenderableType]:
    needs_attention = (
        any(not s.active for s in sync_statuses.values())
        or any(
            (set(s.errors) - INACTIVE_SSH_ERRORS) or s.warnings
            for s in ssh_statuses.values()
        )
        or any(s.destination_endpoint_status.warnings for s in sync_statuses.values())
    )
    return (
        [
            Text(""),
            Text.assemble(
                "Run ",
                (invocation.command("preflight", "troubleshoot"), "bold"),
                " for detailed remediation steps.",
            ),
        ]
        if needs_attention
        else []
    )
