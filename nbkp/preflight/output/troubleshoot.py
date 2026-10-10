"""Troubleshoot output: per-error remediation instructions for all 4 layers.

Issues are first *collected* into structured :class:`TroubleshootIssue`
records (pure, order-preserving, deduplicated), then either rendered for
humans — grouped under a header per subject — or emitted as JSON with the
same remediation text in plain form.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO

from rich.console import Console

from ...config import Config, SyncConfig
from ...config.epresolution import ResolvedEndpoints
from ...policy import Severity, Strictness
from ..severity import PreflightError, severity_for_error
from ..status import (
    DestinationEndpointError,
    DestinationEndpointStatus,
    SourceEndpointError,
    SourceEndpointStatus,
    SshEndpointError,
    SshEndpointStatus,
    SshEndpointWarning,
    SyncError,
    SyncStatus,
    VolumeError,
    VolumeStatus,
)
from .remediation import (
    ERROR,
    HEADER,
    TroubleshootContext,
    print_destination_endpoint_error_fix,
    print_source_endpoint_error_fix,
    print_ssh_endpoint_error_fix,
    print_ssh_endpoint_warning_fix,
    print_sync_error_fix,
    print_volume_error_fix,
    say,
)

# Cascade errors are pointers to inactive lower layers — they have no
# actionable fix at their own layer, so troubleshoot skips them.
_CASCADE_ERRORS: frozenset[PreflightError] = frozenset(
    {
        VolumeError.SSH_ENDPOINT_INACTIVE,
        SourceEndpointError.VOLUME_INACTIVE,
        DestinationEndpointError.VOLUME_INACTIVE,
        SyncError.SOURCE_ENDPOINT_INACTIVE,
        SyncError.DESTINATION_ENDPOINT_INACTIVE,
    }
)


class Layer(str, enum.Enum):
    """Where an issue originates; also the human section header prefix."""

    SSH_ENDPOINT = "ssh-endpoint"
    VOLUME = "volume"
    SOURCE_ENDPOINT = "source-endpoint"
    DESTINATION_ENDPOINT = "destination-endpoint"
    SYNC = "sync"


_HEADERS: dict[Layer, str] = {
    Layer.SSH_ENDPOINT: "SSH Endpoint",
    Layer.VOLUME: "Volume",
    Layer.SOURCE_ENDPOINT: "Source Endpoint",
    Layer.DESTINATION_ENDPOINT: "Destination Endpoint",
    Layer.SYNC: "Sync",
}


@dataclass(frozen=True)
class TroubleshootIssue:
    """One error (or warning) to explain, with what its fix printer needs."""

    layer: Layer
    subject: str
    """Slug of the SSH endpoint / volume / sync endpoint / sync."""
    error: PreflightError | SshEndpointWarning
    severity: Severity
    ssh_status: SshEndpointStatus | None = None
    vol_status: VolumeStatus | None = None
    sync: SyncConfig | None = None
    """A sync using the endpoint (endpoint layers) or the sync itself."""


# ── Collection ────────────────────────────────────────────────


def _ssh_issues(
    ssh_statuses: dict[str, SshEndpointStatus], strictness: Strictness
) -> list[TroubleshootIssue]:
    return [
        *(
            TroubleshootIssue(
                Layer.SSH_ENDPOINT,
                status.slug,
                error,
                severity_for_error(error, strictness),
                ssh_status=status,
            )
            for status in ssh_statuses.values()
            for error in status.errors
        ),
        *(
            TroubleshootIssue(
                Layer.SSH_ENDPOINT,
                status.slug,
                warning,
                Severity.WARNING,
                ssh_status=status,
            )
            for status in ssh_statuses.values()
            for warning in status.warnings
        ),
    ]


def _volume_issues(
    vol_statuses: dict[str, VolumeStatus], strictness: Strictness
) -> list[TroubleshootIssue]:
    return [
        TroubleshootIssue(
            Layer.VOLUME,
            vs.slug,
            error,
            severity_for_error(error, strictness),
            vol_status=vs,
        )
        for vs in vol_statuses.values()
        for error in vs.errors
        if error not in _CASCADE_ERRORS
    ]


def _endpoint_issues(
    layer: Layer,
    endpoints: list[
        tuple[SourceEndpointStatus | DestinationEndpointStatus, SyncConfig]
    ],
    strictness: Strictness,
) -> list[TroubleshootIssue]:
    """Issues of endpoints shared by several syncs, reported once.

    Deduplicated per ``(endpoint, error)`` — the first sync using the
    endpoint provides the fix context.
    """
    candidates = [
        ((ep.endpoint_slug, error), sync)
        for ep, sync in endpoints
        for error in ep.errors
        if error not in _CASCADE_ERRORS
    ]
    first = {key: sync for key, sync in reversed(candidates)}
    return [
        TroubleshootIssue(
            layer, slug, error, severity_for_error(error, strictness), sync=first[key]
        )
        for key in dict.fromkeys(key for key, _ in candidates)
        for slug, error in [key]
    ]


def _sync_issues(
    sync_statuses: dict[str, SyncStatus], strictness: Strictness
) -> list[TroubleshootIssue]:
    return [
        TroubleshootIssue(
            Layer.SYNC,
            ss.slug,
            error,
            severity_for_error(error, strictness),
            sync=ss.config,
        )
        for ss in sync_statuses.values()
        for error in ss.errors
        if error not in _CASCADE_ERRORS
    ]


def collect_issues(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[TroubleshootIssue]:
    """Every actionable issue, layer by layer, in display order."""
    return [
        *_ssh_issues(ssh_statuses, strictness),
        *_volume_issues(vol_statuses, strictness),
        *_endpoint_issues(
            Layer.SOURCE_ENDPOINT,
            [(ss.source_endpoint_status, ss.config) for ss in sync_statuses.values()],
            strictness,
        ),
        *_endpoint_issues(
            Layer.DESTINATION_ENDPOINT,
            [
                (ss.destination_endpoint_status, ss.config)
                for ss in sync_statuses.values()
            ],
            strictness,
        ),
        *_sync_issues(sync_statuses, strictness),
    ]


# ── Rendering ─────────────────────────────────────────────────


def print_issue_fix(
    console: Console, issue: TroubleshootIssue, ctx: TroubleshootContext
) -> None:
    """Dispatch to the fix printer of the issue's layer."""
    match issue:
        case TroubleshootIssue(
            error=SshEndpointWarning() as warning, ssh_status=SshEndpointStatus() as st
        ):
            print_ssh_endpoint_warning_fix(console, st, warning)
        case TroubleshootIssue(
            error=SshEndpointError() as error, ssh_status=SshEndpointStatus() as st
        ):
            print_ssh_endpoint_error_fix(console, st, error, ctx)
        case TroubleshootIssue(
            error=VolumeError() as error, vol_status=VolumeStatus() as vs
        ):
            print_volume_error_fix(console, vs, error, ctx)
        case TroubleshootIssue(
            error=SourceEndpointError() as error, sync=SyncConfig() as sync
        ):
            print_source_endpoint_error_fix(console, error, sync, ctx)
        case TroubleshootIssue(
            error=DestinationEndpointError() as error, sync=SyncConfig() as sync
        ):
            print_destination_endpoint_error_fix(console, error, sync, ctx)
        case TroubleshootIssue(error=SyncError() as error, sync=SyncConfig() as sync):
            print_sync_error_fix(console, sync, error, ctx)
        case _:
            pass


def _issue_label(issue: TroubleshootIssue) -> tuple[str, str]:
    """``(text, style)`` for the issue line; warnings say so."""
    return (
        (f"warning: {issue.error.value}", "yellow")
        if isinstance(issue.error, SshEndpointWarning)
        else (issue.error.value, "")
    )


def _print_group(
    console: Console,
    layer: Layer,
    subject: str,
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
) -> None:
    console.print()
    say(
        console,
        HEADER,
        (f"{_HEADERS[layer]} ", "bold"),
        (repr(subject), "bold"),
        (":", "bold"),
    )
    for issue in issues:
        say(console, ERROR, _issue_label(issue))
        print_issue_fix(console, issue, ctx)


def print_issues(
    console: Console,
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
) -> None:
    """Print issues grouped under one header per (layer, subject)."""
    groups = list(dict.fromkeys((i.layer, i.subject) for i in issues))
    for layer, subject in groups:
        _print_group(
            console,
            layer,
            subject,
            [i for i in issues if (i.layer, i.subject) == (layer, subject)],
            ctx,
        )


def print_human_troubleshoot(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    context: TroubleshootContext | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> None:
    """Print troubleshooting instructions for all 4 layers.

    Iterates through SSH endpoints, volumes, sync endpoints (source
    and destination), and syncs, printing fix instructions for each
    error at the layer where it originates.  *context* carries the
    invocation flags and local user used in suggested commands.
    """
    con = console if console is not None else Console()
    ctx = context or TroubleshootContext(
        config=config, resolved_endpoints=resolved_endpoints or {}
    )
    issues = collect_issues(ssh_statuses, vol_statuses, sync_statuses, strictness)
    if issues:
        print_issues(con, issues, ctx)
    else:
        con.print("No issues found. All volumes and syncs are active.")


# ── JSON ──────────────────────────────────────────────────────


def _render_plain(render: Callable[[Console], None]) -> str:
    """Capture what *render* prints, without styling, as plain text."""
    buf = StringIO()
    render(Console(file=buf, width=100, color_system=None, highlight=False))
    return buf.getvalue().rstrip("\n")


def troubleshoot_json(
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
    *,
    has_fatal_errors: bool,
) -> dict[str, object]:
    """Structured troubleshoot output: one entry per issue.

    ``code`` is the stable enum member name (``RSYNC_NOT_FOUND``) and the
    identifier to branch on; ``remediation`` is the human fix as plain text.
    """
    return {
        "has_fatal_errors": has_fatal_errors,
        "issues": [
            {
                "layer": issue.layer.value,
                "subject": issue.subject,
                "kind": (
                    "warning"
                    if isinstance(issue.error, SshEndpointWarning)
                    else "error"
                ),
                "code": issue.error.name,
                "message": issue.error.value,
                "severity": issue.severity.value,
                "remediation": _render_plain(
                    lambda con, issue=issue: print_issue_fix(con, issue, ctx)
                ),
            }
            for issue in issues
        ],
    }
