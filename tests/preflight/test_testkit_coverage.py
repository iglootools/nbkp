"""The troubleshoot demo data must exercise every preflight error, and
``troubleshoot`` must print a fix for each.

``nbkp demo output`` renders ``troubleshoot_data`` so that every remediation
can be eyeballed; an enum member missing from it is a fix nobody ever looks
at.  The fix printers end in a catch-all ``case _`` that returns no lines or
a generic pointer, so a new member without a ``case`` of its own would
otherwise render an empty or unhelpful fix.  This guards the checklist item
in docs/guidelines.md.
"""

import enum
from functools import cache

import pytest

from nbkp.preflight import (
    DestinationEndpointErrorKind,
    DestinationEndpointWarningKind,
    SourceEndpointErrorKind,
    SshEndpointErrorKind,
    SyncErrorKind,
    VolumeErrorKind,
)
from nbkp.preflight.output import (
    RemediationFacts,
    TroubleshootContext,
    collect_issues,
    troubleshoot_json,
)
from nbkp.preflight.status.ssh import SshEndpointWarningKind
from nbkp.preflight.testkit import troubleshoot_config, troubleshoot_data
from nbkp.remote.resolution import resolve_all_endpoints

# Cascade pointers have no fix of their own; troubleshoot skips them.
_CASCADE = {
    VolumeErrorKind.SSH_ENDPOINT_INACTIVE,
    SourceEndpointErrorKind.VOLUME_INACTIVE,
    DestinationEndpointErrorKind.VOLUME_INACTIVE,
    SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
    SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
}


@cache
def _remediations() -> dict[enum.Enum, tuple[str, ...]]:
    """Each rendered error → the plain-text fix of each of its issues."""
    config = troubleshoot_config()
    data = troubleshoot_data(config)
    issues = collect_issues(
        data.ssh_endpoint_statuses, data.volume_statuses, data.sync_statuses
    )
    # Facts given up front: gathering them would probe hosts for a mapper name.
    ctx = TroubleshootContext(
        config=config,
        resolved_endpoints=resolve_all_endpoints(config),
        facts=RemediationFacts(),
    )
    rendered = troubleshoot_json(issues, ctx, has_fatal_errors=True)["issues"]
    assert isinstance(rendered, list)
    pairs = [
        (issue.error, item["remediation"]) for issue, item in zip(issues, rendered)
    ]
    return {error: tuple(fix for e, fix in pairs if e is error) for error, _ in pairs}


_REPORTED_ERRORS = pytest.mark.parametrize(
    "error",
    [
        member
        for enum_type in (
            SshEndpointErrorKind,
            SshEndpointWarningKind,
            VolumeErrorKind,
            SourceEndpointErrorKind,
            DestinationEndpointErrorKind,
            DestinationEndpointWarningKind,
            SyncErrorKind,
        )
        for member in enum_type
        if member not in _CASCADE
    ],
    ids=lambda e: f"{type(e).__name__}.{e.name}",
)


@_REPORTED_ERRORS
def test_troubleshoot_data_covers_error(error: enum.Enum) -> None:
    assert error in _remediations()


@_REPORTED_ERRORS
def test_troubleshoot_prints_a_fix(error: enum.Enum) -> None:
    fixes = _remediations().get(error, ())
    assert fixes, "not rendered: see test_troubleshoot_data_covers_error"
    assert all(fix.strip() for fix in fixes)
