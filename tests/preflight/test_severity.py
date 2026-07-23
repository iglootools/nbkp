"""Tests for preflight error severity classification."""

from __future__ import annotations

from nbkp.clihelpers import Severity
from nbkp.preflight.severity import severity_for_error, severity_for_errors
from nbkp.preflight.status import (
    DestinationEndpointError,
    SourceEndpointError,
    SshEndpointError,
    SyncError,
    VolumeError,
)
from nbkp.preflight.strictness import Strictness


class TestSeverityForError:
    def test_ignore_inactive_inactive_error_is_warning(self) -> None:
        """Under IGNORE_INACTIVE, errors in the inactive set are warnings."""
        assert (
            severity_for_error(
                VolumeError.SENTINEL_NOT_FOUND, Strictness.IGNORE_INACTIVE
            )
            is Severity.WARNING
        )
        assert (
            severity_for_error(
                VolumeError.DEVICE_NOT_PRESENT, Strictness.IGNORE_INACTIVE
            )
            is Severity.WARNING
        )

    def test_ignore_inactive_infra_error_is_error(self) -> None:
        """Under IGNORE_INACTIVE, infrastructure errors stay fatal."""
        assert (
            severity_for_error(
                DestinationEndpointError.NOT_WRITABLE, Strictness.IGNORE_INACTIVE
            )
            is Severity.ERROR
        )
        assert (
            severity_for_error(VolumeError.UNLOCK_FAILED, Strictness.IGNORE_INACTIVE)
            is Severity.ERROR
        )

    def test_ignore_none_everything_fatal(self) -> None:
        """Under IGNORE_NONE, even inactive errors are fatal."""
        assert (
            severity_for_error(VolumeError.SENTINEL_NOT_FOUND, Strictness.IGNORE_NONE)
            is Severity.ERROR
        )

    def test_ignore_all_nothing_fatal(self) -> None:
        """Under IGNORE_ALL, even infrastructure errors are warnings."""
        assert (
            severity_for_error(
                DestinationEndpointError.NOT_WRITABLE, Strictness.IGNORE_ALL
            )
            is Severity.WARNING
        )

    def test_each_layer_classifier(self) -> None:
        """Sanity-check that each enum layer routes through correctly."""
        # SSH: UNREACHABLE is inactive
        assert (
            severity_for_error(SshEndpointError.UNREACHABLE, Strictness.IGNORE_INACTIVE)
            is Severity.WARNING
        )
        # Source EP: SENTINEL_NOT_FOUND is inactive
        assert (
            severity_for_error(
                SourceEndpointError.SENTINEL_NOT_FOUND, Strictness.IGNORE_INACTIVE
            )
            is Severity.WARNING
        )
        # Dest EP: SENTINEL_NOT_FOUND is inactive
        assert (
            severity_for_error(
                DestinationEndpointError.SENTINEL_NOT_FOUND,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )
        # Sync: SOURCE_ENDPOINT_INACTIVE is inactive
        assert (
            severity_for_error(
                SyncError.SOURCE_ENDPOINT_INACTIVE, Strictness.IGNORE_INACTIVE
            )
            is Severity.WARNING
        )


class TestSeverityForErrors:
    def test_empty_is_ok(self) -> None:
        assert severity_for_errors([], Strictness.IGNORE_INACTIVE) is Severity.OK

    def test_all_warnings_is_warning(self) -> None:
        assert (
            severity_for_errors(
                [
                    VolumeError.SENTINEL_NOT_FOUND,
                    VolumeError.DEVICE_NOT_PRESENT,
                ],
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )

    def test_any_error_makes_it_error(self) -> None:
        """A single fatal error elevates the overall severity to ERROR."""
        assert (
            severity_for_errors(
                [
                    VolumeError.SENTINEL_NOT_FOUND,  # warning
                    VolumeError.UNLOCK_FAILED,  # error
                ],
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.ERROR
        )

    def test_strictness_changes_classification(self) -> None:
        """Same errors classify differently under different strictness."""
        errors: list = [VolumeError.SENTINEL_NOT_FOUND]
        assert (
            severity_for_errors(errors, Strictness.IGNORE_INACTIVE) is Severity.WARNING
        )
        assert severity_for_errors(errors, Strictness.IGNORE_NONE) is Severity.ERROR
        assert severity_for_errors(errors, Strictness.IGNORE_ALL) is Severity.WARNING
