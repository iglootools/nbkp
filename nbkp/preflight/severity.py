"""
Map preflight errors to display severity given a strictness policy.

The interpretation of "is this error fatal?" depends on the
``Strictness`` mode in use.  This module turns each error into a
``Severity`` so the check display can pick the right icon/color
without re-implementing the policy at every call site.
"""

from nbkp.policy import Severity, Strictness, classify_severity
from nbkp.preflight.status.endpoint import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    DestinationEndpointErrorKind,
    DestinationEndpointWarningKind,
    SourceEndpointErrorKind,
)
from nbkp.preflight.status.ssh import (
    INACTIVE_SSH_ERRORS,
    SshEndpointErrorKind,
    SshEndpointWarningKind,
)
from nbkp.preflight.status.sync import INACTIVE_SYNC_ERRORS, SyncErrorKind
from nbkp.preflight.status.volume import INACTIVE_VOLUME_ERRORS, VolumeErrorKind

PreflightErrorKind = (
    SshEndpointErrorKind
    | VolumeErrorKind
    | SourceEndpointErrorKind
    | DestinationEndpointErrorKind
    | SyncErrorKind
)

PreflightWarningKind = SshEndpointWarningKind | DestinationEndpointWarningKind
"""Findings shown as warnings that never make anything inactive."""


def _is_inactive(error: PreflightErrorKind) -> bool:
    match error:
        case SshEndpointErrorKind():
            return error in INACTIVE_SSH_ERRORS
        case VolumeErrorKind():
            return error in INACTIVE_VOLUME_ERRORS
        case SourceEndpointErrorKind():
            return error in INACTIVE_SRC_ENDPOINT_ERRORS
        case DestinationEndpointErrorKind():
            return error in INACTIVE_DST_ENDPOINT_ERRORS
        case SyncErrorKind():
            return error in INACTIVE_SYNC_ERRORS


def severity_for_error(error: PreflightErrorKind, strictness: Strictness) -> Severity:
    """
    Classify a single error as fatal (ERROR) or non-fatal (WARNING).

    Under ``IGNORE_NONE`` every error is fatal; under ``IGNORE_ALL``
    nothing is fatal; under ``IGNORE_INACTIVE`` (default) only the
    "expected inactive" set is treated as non-fatal.  A disabled sync is a
    warning under every mode, as :func:`~nbkp.preflight.strictness.is_fatal`
    never lets it fail a run.
    """
    return (
        Severity.WARNING
        if error is SyncErrorKind.DISABLED
        else classify_severity(is_inactive=_is_inactive(error), strictness=strictness)
    )


def severity_for_errors(
    errors: list[PreflightErrorKind]
    | list[SshEndpointErrorKind]
    | list[VolumeErrorKind]
    | list[SourceEndpointErrorKind]
    | list[DestinationEndpointErrorKind]
    | list[SyncErrorKind],
    strictness: Strictness,
) -> Severity:
    """
    Highest severity across the given errors.

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
