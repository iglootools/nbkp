"""Tests for sync result → severity mapping (display icon and exit code)."""

from __future__ import annotations

import subprocess

import pytest

from nbkp.policy import Severity, Strictness
from nbkp.sync.results import SyncFailureKind, SyncResult, result_severity

_OK_PROC = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _success() -> SyncResult:
    return SyncResult.succeeded("s", False, _OK_PROC)


def _failed() -> SyncResult:
    return SyncResult.failed("s", False, SyncFailureKind.RSYNC, "boom")


def _skipped(kind: SyncFailureKind) -> SyncResult:
    return SyncResult.skipped("s", False, kind, "not active")


def _cancelled(kind: SyncFailureKind) -> SyncResult:
    return SyncResult.cancelled("s", False, kind, "up")


ALL_STRICTNESS = list(Strictness)


class TestRuntimeOutcomes:
    @pytest.mark.parametrize("strictness", ALL_STRICTNESS)
    def test_success_is_ok(self, strictness: Strictness) -> None:
        assert result_severity(_success(), strictness) is Severity.OK

    @pytest.mark.parametrize("strictness", ALL_STRICTNESS)
    def test_failed_is_error(self, strictness: Strictness) -> None:
        """A runtime failure is an error regardless of strictness."""
        assert result_severity(_failed(), strictness) is Severity.ERROR

    @pytest.mark.parametrize("strictness", ALL_STRICTNESS)
    def test_cancelled_by_failed_upstream_is_error(
        self, strictness: Strictness
    ) -> None:
        result = _cancelled(SyncFailureKind.UPSTREAM_FAILED)
        assert result_severity(result, strictness) is Severity.ERROR


class TestInactiveRootedOutcomes:
    """Skips and cancellations rooted in expected inactivity."""

    @pytest.mark.parametrize(
        "result",
        [
            _skipped(SyncFailureKind.INACTIVE),
            _skipped(SyncFailureKind.DRY_RUN_PENDING),
            _cancelled(SyncFailureKind.UPSTREAM_SKIPPED),
        ],
    )
    @pytest.mark.parametrize(
        ("strictness", "expected"),
        [
            (Strictness.IGNORE_INACTIVE, Severity.WARNING),
            (Strictness.IGNORE_ALL, Severity.WARNING),
            (Strictness.IGNORE_NONE, Severity.ERROR),
        ],
    )
    def test_follow_inactive_policy(
        self, result: SyncResult, strictness: Strictness, expected: Severity
    ) -> None:
        assert result_severity(result, strictness) is expected


class TestPreflightSkip:
    """Skipped for infrastructure errors: only ignore-all tolerates it."""

    @pytest.mark.parametrize(
        ("strictness", "expected"),
        [
            (Strictness.IGNORE_INACTIVE, Severity.ERROR),
            (Strictness.IGNORE_ALL, Severity.WARNING),
            (Strictness.IGNORE_NONE, Severity.ERROR),
        ],
    )
    def test_follows_infrastructure_policy(
        self, strictness: Strictness, expected: Severity
    ) -> None:
        result = _skipped(SyncFailureKind.PREFLIGHT)
        assert result_severity(result, strictness) is expected


class TestSyncResultModel:
    def test_success_is_derived_from_outcome(self) -> None:
        assert _success().success is True
        assert _failed().success is False

    def test_json_keeps_success_field(self) -> None:
        dumped = _failed().model_dump(mode="json")
        assert dumped["success"] is False
        assert dumped["outcome"] == "failed"
        assert dumped["failure"] == "rsync"

    def test_inconsistent_outcome_rejected(self) -> None:
        with pytest.raises(ValueError, match="contradicts"):
            SyncResult(
                sync_slug="s",
                outcome="success",  # type: ignore[arg-type]
                dry_run=False,
                failure=SyncFailureKind.RSYNC,
            )

    def test_cancelled_by_required_for_cancellation(self) -> None:
        with pytest.raises(ValueError, match="cancelled_by"):
            SyncResult(
                sync_slug="s",
                outcome="cancelled",  # type: ignore[arg-type]
                dry_run=False,
                failure=SyncFailureKind.UPSTREAM_FAILED,
            )

    def test_frozen(self) -> None:
        result = _success()
        with pytest.raises(ValueError, match="frozen"):
            result.detail = "x"  # type: ignore[misc]
