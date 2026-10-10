"""The troubleshoot demo data must exercise every preflight error.

``nbkp demo output`` renders ``troubleshoot_data`` so that every remediation
can be eyeballed; an enum member missing from it is a fix nobody ever looks
at.  This guards the checklist item in docs/guidelines.md.
"""

from __future__ import annotations

import enum
from functools import cache

import pytest

from nbkp.preflight import (
    DestinationEndpointError,
    SourceEndpointError,
    SshEndpointError,
    SyncError,
    VolumeError,
)
from nbkp.preflight.output import collect_issues
from nbkp.preflight.status import SshEndpointWarning
from nbkp.preflight.testkit import troubleshoot_config, troubleshoot_data

# Cascade pointers have no fix of their own; troubleshoot skips them.
_CASCADE = {
    VolumeError.SSH_ENDPOINT_INACTIVE,
    SourceEndpointError.VOLUME_INACTIVE,
    DestinationEndpointError.VOLUME_INACTIVE,
    SyncError.SOURCE_ENDPOINT_INACTIVE,
    SyncError.DESTINATION_ENDPOINT_INACTIVE,
}


@cache
def _rendered_errors() -> frozenset[enum.Enum]:
    config = troubleshoot_config()
    data = troubleshoot_data(config)
    issues = collect_issues(
        data.ssh_endpoint_statuses, data.volume_statuses, data.sync_statuses
    )
    return frozenset(issue.error for issue in issues)


@pytest.mark.parametrize(
    "error",
    [
        member
        for enum_type in (
            SshEndpointError,
            SshEndpointWarning,
            VolumeError,
            SourceEndpointError,
            DestinationEndpointError,
            SyncError,
        )
        for member in enum_type
        if member not in _CASCADE
    ],
    ids=lambda e: f"{type(e).__name__}.{e.name}",
)
def test_troubleshoot_data_covers_error(error: enum.Enum) -> None:
    assert error in _rendered_errors()
