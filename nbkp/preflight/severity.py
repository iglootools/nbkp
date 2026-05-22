"""Map preflight errors to display severity given a strictness policy.

The interpretation of "is this error fatal?" depends on the
``Strictness`` mode in use.  This module turns each error into a
``Severity`` so the check display can pick the right icon/color
without re-implementing the policy at every call site.
"""

from __future__ import annotations

from ..clihelpers import Severity, classify_severity
from .status import (
    DestinationEndpointError,
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    INACTIVE_SSH_ERRORS,
    INACTIVE_SYNC_ERRORS,
    INACTIVE_VOLUME_ERRORS,
    SourceEndpointError,
    SshEndpointError,
    SyncError,
    VolumeError,
)
from .strictness import Strictness

PreflightError = (
    SshEndpointError
    | VolumeError
    | SourceEndpointError
    | DestinationEndpointError
    | SyncError
)


def _is_inactive(error: PreflightError) -> bool:
    """Whether an error is in the 'expected inactive' category at its layer."""
    match error:
        case SshEndpointError():
            return error in INACTIVE_SSH_ERRORS
        case VolumeError():
            return error in INACTIVE_VOLUME_ERRORS
        case SourceEndpointError():
            return error in INACTIVE_SRC_ENDPOINT_ERRORS
        case DestinationEndpointError():
            return error in INACTIVE_DST_ENDPOINT_ERRORS
        case SyncError():
            return error in INACTIVE_SYNC_ERRORS


def severity_for_error(error: PreflightError, strictness: Strictness) -> Severity:
    """Classify a single error as fatal (ERROR) or non-fatal (WARNING).

    Under ``IGNORE_NONE`` every error is fatal; under ``IGNORE_ALL``
    nothing is fatal; under ``IGNORE_INACTIVE`` (default) only the
    "expected inactive" set is treated as non-fatal.
    """
    return classify_severity(_is_inactive(error), strictness)


def severity_for_errors(
    errors: list[PreflightError]
    | list[SshEndpointError]
    | list[VolumeError]
    | list[SourceEndpointError]
    | list[DestinationEndpointError]
    | list[SyncError],
    strictness: Strictness,
) -> Severity:
    """Highest severity across the given errors.

    Returns ``OK`` when the list is empty.  Otherwise returns
    ``ERROR`` if any error is fatal, else ``WARNING``.
    """
    if not errors:
        return Severity.OK
    return (
        Severity.ERROR
        if any(severity_for_error(e, strictness) is Severity.ERROR for e in errors)
        else Severity.WARNING
    )
