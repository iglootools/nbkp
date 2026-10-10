"""Severity of the mount-state columns (device, LUKS, mounted).

Decisions, not display: shared by the disks status table and the preflight
check display, which render the result with their own icons.
"""

from __future__ import annotations

from ..policy import Severity, Strictness, classify_severity
from .lifecycle import LUKS_STAGE_FAILURES, MOUNT_STAGE_FAILURES
from .models import MountFailureReason

# ── Mount-state column severity ─────────────────────────────────
#
# These helpers are the single source of truth for severity in mount-state
# columns (``device``, ``LUKS``, ``mounted``) across both the disks status
# table and the preflight check display.  Each answers the question "did
# the action for this column actually attempt and fail?" using
# ``mount_failure_reason``, then routes the answer through
# ``classify_severity`` so strictness is respected.
#
# Note: the "is this a real failure?" rule lives here, separate from the
# preflight ``INACTIVE_*_ERRORS`` frozensets, because the mount-state
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
    mount_failure_reason: MountFailureReason | None,
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
    mount_failure_reason: MountFailureReason | None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity for ``mounted=False``.

    Real mount-stage failures (``MOUNT_FAILED`` / ``NOT_AUTHORIZED``)
    are non-inactive.  Other states (probe-only, or a cascade from an
    earlier step) are inactive.
    """
    is_real_failure = mount_failure_reason in MOUNT_STAGE_FAILURES
    return classify_severity(is_inactive=not is_real_failure, strictness=strictness)
