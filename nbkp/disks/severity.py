"""Severity of mount results and of the mount-state columns.

Decisions, not display: shared by the mount progress bars, the disks status
table and the preflight check display, which render the result with their own
icons.
"""

from nbkp.disks.lifecycle import LUKS_STAGE_FAILURES, MOUNT_STAGE_FAILURES, MountResult
from nbkp.disks.models import MountFailureKind
from nbkp.policy import Severity, Strictness, classify_severity

# Each helper answers "did the action for this column actually attempt and
# fail?" from ``mount_failure_reason``, then routes the answer through
# ``classify_severity`` so strictness is respected.  That rule lives here rather
# than in the preflight ``INACTIVE_*_ERRORS`` frozensets because the mount-state
# table answers a different question than general preflight error
# classification (action-was-attempted vs. policy-inactive).


def device_fail_severity(
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``device_present=False``.

    A missing device is by definition an "observation, not failure"
    state, so it's always inactive at this layer and the strictness
    policy decides the final icon.
    """
    return classify_severity(is_inactive=True, strictness=strictness)


def luks_fail_severity(
    mount_failure_reason: MountFailureKind | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``luks_unlocked=False``.

    Real LUKS-stage failures (``PASSPHRASE_NOT_AVAILABLE`` /
    ``UNLOCK_FAILED`` / ``NOT_AUTHORIZED``)
    are non-inactive — they're real errors and surface as ✗ under any
    strictness that doesn't ignore everything.  Other states (probe
    found the cleartext device missing but no unlock was attempted,
    or a cascading failure like ``DEVICE_NOT_PRESENT``) are inactive.
    """
    is_real_failure = mount_failure_reason in LUKS_STAGE_FAILURES
    return classify_severity(is_inactive=not is_real_failure, strictness=strictness)


def mounted_fail_severity(
    mount_failure_reason: MountFailureKind | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``mounted=False``.

    Real mount-stage failures (``MOUNT_FAILED`` / ``NOT_AUTHORIZED``)
    are non-inactive.  Other states (probe-only, or a cascade from an
    earlier step) are inactive.
    """
    is_real_failure = mount_failure_reason in MOUNT_STAGE_FAILURES
    return classify_severity(is_inactive=not is_real_failure, strictness=strictness)


# Mount failure reasons that correspond to "expected inactive" preflight
# states (e.g. drive not plugged in maps to VolumeErrorKind.DEVICE_NOT_PRESENT
# in INACTIVE_VOLUME_ERRORS; UNREACHABLE maps to SshEndpointErrorKind.UNREACHABLE
# in INACTIVE_SSH_ERRORS).  Everything else — PASSPHRASE_NOT_AVAILABLE on a
# plugged-in drive included — is a real failure.
_INACTIVE_MOUNT_REASONS: frozenset[MountFailureKind] = frozenset(
    {
        MountFailureKind.DEVICE_NOT_PRESENT,
        MountFailureKind.UNREACHABLE,
    }
)


def mount_result_severity(
    result: MountResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Map a mount lifecycle result to display severity under *strictness*.

    A drive not being plugged in is an expected condition (the user
    backs up to removable media), so under the default
    ``IGNORE_INACTIVE`` it renders as a warning.  Under ``IGNORE_NONE``
    every mount failure is fatal, so it renders as an error to stay
    consistent with the preflight abort that will follow.  Under
    ``IGNORE_ALL`` every mount failure is non-fatal and renders as a
    warning.
    """
    return (
        Severity.OK
        if result.success
        else classify_severity(
            result.failure_reason in _INACTIVE_MOUNT_REASONS, strictness
        )
    )
