"""
Troubleshoot output: per-error remediation instructions for all 4 layers.

Renders the issues :func:`nbkp.preflight.troubleshoot.collect_issues`
collected, with the facts
:func:`nbkp.preflight.troubleshoot.gather_remediation_facts` gathered and the
caller put in the :class:`TroubleshootContext`: for humans, grouped under a
header per subject, or as JSON with the same remediation text in plain form.
Rendering does no I/O.
"""

from io import StringIO

from rich.console import Console, Group, RenderableType
from rich.text import Text

from nbkp.config import (
    Config,
    SyncConfig,
)
from nbkp.policy import Strictness
from nbkp.preflight.output.messages import format_problem
from nbkp.preflight.output.remediation import (
    ERROR,
    HEADER,
    Lines,
    TroubleshootContext,
    format_destination_endpoint_error_fix,
    format_destination_endpoint_warning_fix,
    format_line,
    format_source_endpoint_error_fix,
    format_ssh_endpoint_error_fix,
    format_ssh_endpoint_warning_fix,
    format_sync_error_fix,
    format_volume_error_fix,
)
from nbkp.preflight.severity import (
    PreflightWarningKind,
)
from nbkp.preflight.status.endpoint import (
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    DestinationEndpointWarningKind,
    SourceEndpointErrorKind,
)
from nbkp.preflight.status.ssh import (
    SshEndpointErrorKind,
    SshEndpointStatus,
    SshEndpointWarningKind,
)
from nbkp.preflight.status.sync import SyncErrorKind, SyncStatus
from nbkp.preflight.status.volume import VolumeErrorKind, VolumeStatus
from nbkp.preflight.troubleshoot import Layer, TroubleshootIssue, collect_issues
from nbkp.remote.endpoints import ResolvedEndpoints

_HEADERS: dict[Layer, str] = {
    Layer.SSH_ENDPOINT: "SSH Endpoint",
    Layer.VOLUME: "Volume",
    Layer.SOURCE_ENDPOINT: "Source Endpoint",
    Layer.DESTINATION_ENDPOINT: "Destination Endpoint",
    Layer.SYNC: "Sync",
}


def format_issue_fix(issue: TroubleshootIssue, ctx: TroubleshootContext) -> Lines:
    """
    Return the remediation lines for *issue*, by the kind of its error.

    Empty when the issue lacks the status or sync its kind of fix needs.
    """
    match issue:
        case TroubleshootIssue(
            error=SshEndpointWarningKind() as warning,
            ssh_status=SshEndpointStatus() as st,
        ):
            return format_ssh_endpoint_warning_fix(st, warning)
        case TroubleshootIssue(
            error=DestinationEndpointWarningKind() as warning,
            sync=SyncConfig() as sync,
            dst_status=DestinationEndpointStatus() as st,
        ):
            return format_destination_endpoint_warning_fix(warning, sync, st, ctx)
        case TroubleshootIssue(
            error=SshEndpointErrorKind() as error,
            ssh_status=SshEndpointStatus() as st,
        ):
            return format_ssh_endpoint_error_fix(st, error, ctx)
        case TroubleshootIssue(
            error=VolumeErrorKind() as error,
            vol_status=VolumeStatus() as vs,
        ):
            return format_volume_error_fix(vs, error, ctx)
        case TroubleshootIssue(
            error=SourceEndpointErrorKind() as error,
            sync=SyncConfig() as sync,
        ):
            return format_source_endpoint_error_fix(error, sync, ctx)
        case TroubleshootIssue(
            error=DestinationEndpointErrorKind() as error,
            sync=SyncConfig() as sync,
        ):
            return format_destination_endpoint_error_fix(error, sync, ctx)
        case TroubleshootIssue(
            error=SyncErrorKind() as error,
            sync=SyncConfig() as sync,
        ):
            return format_sync_error_fix(sync, error, ctx)
        case _:
            return []


def _issue_label(issue: TroubleshootIssue) -> tuple[str, str]:
    return (
        (f"warning: {format_problem(issue.error)}", "yellow")
        if isinstance(issue.error, PreflightWarningKind)
        else (format_problem(issue.error), "")
    )


def _format_group(
    layer: Layer,
    subject: str,
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
) -> Lines:
    return [
        Text(""),
        format_line(
            HEADER,
            (f"{_HEADERS[layer]} ", "bold"),
            (repr(subject), "bold"),
            (":", "bold"),
        ),
        *(
            line
            for issue in issues
            for line in (
                format_line(ERROR, _issue_label(issue)),
                *format_issue_fix(issue, ctx),
            )
        ),
    ]


def format_issues(issues: list[TroubleshootIssue], ctx: TroubleshootContext) -> Group:
    """
    Format the issues, as pure rendering.

    Facts missing from ``ctx.facts`` are rendered as unknown (default mapper name, no
    polkit rule body).
    """
    groups = list(dict.fromkeys((i.layer, i.subject) for i in issues))
    return Group(
        *(
            line
            for layer, subject in groups
            for line in _format_group(
                layer,
                subject,
                [i for i in issues if (i.layer, i.subject) == (layer, subject)],
                ctx,
            )
        ),
    )


def format_human_troubleshoot(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    resolved_endpoints: ResolvedEndpoints | None = None,
    context: TroubleshootContext | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> RenderableType:
    """
    Format the human-readable troubleshoot report.

    *context* carries the invocation flags and local user used in suggested commands,
    and the facts gathered for the fixes.
    """
    ctx = context or TroubleshootContext(
        config=config,
        resolved_endpoints=resolved_endpoints or {},
    )
    issues = collect_issues(ssh_statuses, vol_statuses, sync_statuses, strictness)
    return (
        format_issues(issues, ctx)
        if issues
        else "No issues found. All volumes and syncs are active."
    )


def _render_plain(lines: Lines) -> str:
    buf = StringIO()
    Console(file=buf, width=100, color_system=None, highlight=False).print(
        Group(*lines),
    )
    return buf.getvalue().rstrip("\n")


def troubleshoot_json(
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
    *,
    has_fatal_errors: bool,
) -> dict[str, object]:
    """
    Build the troubleshoot report as JSON-ready data.

    ``code`` is the stable enum member name (``RSYNC_NOT_FOUND``) and the identifier to
    branch on; ``remediation`` is the human fix as plain text, rendered with the facts
    in *ctx*.
    """
    return {
        "has_fatal_errors": has_fatal_errors,
        "issues": [
            {
                "layer": issue.layer.value,
                "subject": issue.subject,
                "kind": (
                    "warning"
                    if isinstance(issue.error, PreflightWarningKind)
                    else "error"
                ),
                "code": issue.error.name,
                "message": format_problem(issue.error),
                "severity": issue.severity.value,
                "remediation": _render_plain(format_issue_fix(issue, ctx)),
            }
            for issue in issues
        ],
    }
