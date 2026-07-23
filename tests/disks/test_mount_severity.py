"""Tests for mount lifecycle severity mapping."""

from __future__ import annotations

from nbkp.clihelpers import Severity, Strictness
from nbkp.disks.cli.helpers.managed_mount import mount_result_severity
from nbkp.disks.lifecycle import MountFailureReason, MountResult


def _result(success: bool, reason: MountFailureReason | None = None) -> MountResult:
    return MountResult(
        volume_slug="vol",
        success=success,
        failure_reason=reason,
    )


class TestMountResultSeverityDefaultStrictness:
    """Under IGNORE_INACTIVE (default), only inactive-class failures warn."""

    def test_success_is_ok(self) -> None:
        assert mount_result_severity(_result(True)) is Severity.OK

    def test_device_not_present_is_warning(self) -> None:
        """A drive being unplugged is expected for removable media."""
        assert (
            mount_result_severity(_result(False, MountFailureReason.DEVICE_NOT_PRESENT))
            is Severity.WARNING
        )

    def test_unreachable_is_warning(self) -> None:
        """SSH unreachable maps to INACTIVE_SSH_ERRORS — also warning."""
        assert (
            mount_result_severity(_result(False, MountFailureReason.UNREACHABLE))
            is Severity.WARNING
        )

    def test_unlock_failure_is_error(self) -> None:
        assert (
            mount_result_severity(_result(False, MountFailureReason.UNLOCK_FAILED))
            is Severity.ERROR
        )

    def test_mount_failure_is_error(self) -> None:
        assert (
            mount_result_severity(_result(False, MountFailureReason.MOUNT_FAILED))
            is Severity.ERROR
        )

    def test_not_authorized_is_error(self) -> None:
        assert (
            mount_result_severity(_result(False, MountFailureReason.NOT_AUTHORIZED))
            is Severity.ERROR
        )

    def test_udisks_not_available_is_error(self) -> None:
        assert (
            mount_result_severity(
                _result(False, MountFailureReason.UDISKS_NOT_AVAILABLE)
            )
            is Severity.ERROR
        )

    def test_no_reason_is_error(self) -> None:
        """Failure without a structured reason still surfaces as an error."""
        assert mount_result_severity(_result(False)) is Severity.ERROR


class TestMountResultSeverityIgnoreNone:
    """Under IGNORE_NONE, every mount failure is fatal (consistent with
    preflight aborting the run shortly afterwards)."""

    def test_device_not_present_is_error(self) -> None:
        assert (
            mount_result_severity(
                _result(False, MountFailureReason.DEVICE_NOT_PRESENT),
                Strictness.IGNORE_NONE,
            )
            is Severity.ERROR
        )

    def test_unreachable_is_error(self) -> None:
        assert (
            mount_result_severity(
                _result(False, MountFailureReason.UNREACHABLE),
                Strictness.IGNORE_NONE,
            )
            is Severity.ERROR
        )


class TestMountResultSeverityIgnoreAll:
    """Under IGNORE_ALL, every mount failure is non-fatal."""

    def test_device_not_present_is_warning(self) -> None:
        assert (
            mount_result_severity(
                _result(False, MountFailureReason.DEVICE_NOT_PRESENT),
                Strictness.IGNORE_ALL,
            )
            is Severity.WARNING
        )

    def test_unlock_failure_is_warning(self) -> None:
        assert (
            mount_result_severity(
                _result(False, MountFailureReason.UNLOCK_FAILED),
                Strictness.IGNORE_ALL,
            )
            is Severity.WARNING
        )
