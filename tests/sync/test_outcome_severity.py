"""Tests for sync outcome → severity mapping."""

from __future__ import annotations

from nbkp.clihelpers import Severity, Strictness
from nbkp.sync.output import outcome_severity
from nbkp.sync.runner import SyncOutcome


class TestOutcomeSeverityDefaultStrictness:
    """Default IGNORE_INACTIVE: preflight-driven non-actions are warnings."""

    def test_success_is_ok(self) -> None:
        assert outcome_severity(SyncOutcome.SUCCESS) is Severity.OK

    def test_skipped_is_warning(self) -> None:
        assert outcome_severity(SyncOutcome.SKIPPED) is Severity.WARNING

    def test_cancelled_is_warning(self) -> None:
        assert outcome_severity(SyncOutcome.CANCELLED) is Severity.WARNING

    def test_failed_is_error(self) -> None:
        """FAILED is a runtime failure, always an error regardless of strictness."""
        assert outcome_severity(SyncOutcome.FAILED) is Severity.ERROR


class TestOutcomeSeverityIgnoreNone:
    """Under IGNORE_NONE, even preflight-driven non-actions are fatal.

    In practice the pipeline aborts before reaching the runner under
    this mode, so SKIPPED / CANCELLED rarely surface — but if they do,
    the icon should agree with the policy.
    """

    def test_skipped_is_error(self) -> None:
        assert (
            outcome_severity(SyncOutcome.SKIPPED, Strictness.IGNORE_NONE)
            is Severity.ERROR
        )

    def test_cancelled_is_error(self) -> None:
        assert (
            outcome_severity(SyncOutcome.CANCELLED, Strictness.IGNORE_NONE)
            is Severity.ERROR
        )

    def test_failed_stays_error(self) -> None:
        assert (
            outcome_severity(SyncOutcome.FAILED, Strictness.IGNORE_NONE)
            is Severity.ERROR
        )


class TestOutcomeSeverityIgnoreAll:
    """Under IGNORE_ALL, preflight-driven non-actions remain non-fatal."""

    def test_skipped_is_warning(self) -> None:
        assert (
            outcome_severity(SyncOutcome.SKIPPED, Strictness.IGNORE_ALL)
            is Severity.WARNING
        )

    def test_failed_still_error(self) -> None:
        """IGNORE_ALL only ignores preflight; runtime failures remain errors."""
        assert (
            outcome_severity(SyncOutcome.FAILED, Strictness.IGNORE_ALL)
            is Severity.ERROR
        )
