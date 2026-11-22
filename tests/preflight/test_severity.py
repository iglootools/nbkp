"""Tests for preflight error severity classification."""

from nbkp.policy import Severity, Strictness
from nbkp.preflight.severity import severity_for_error, severity_for_errors
from nbkp.preflight.status.endpoint import (
    DestinationEndpointErrorKind,
    SourceEndpointErrorKind,
)
from nbkp.preflight.status.ssh import SshEndpointErrorKind
from nbkp.preflight.status.sync import SyncErrorKind
from nbkp.preflight.status.volume import VolumeErrorKind


class TestSeverityForError:
    def test_ignore_inactive_inactive_error_is_warning(self) -> None:
        assert (
            severity_for_error(
                VolumeErrorKind.SENTINEL_NOT_FOUND,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )
        assert (
            severity_for_error(
                VolumeErrorKind.DEVICE_NOT_PRESENT,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )

    def test_ignore_inactive_infra_error_is_error(self) -> None:
        assert (
            severity_for_error(
                DestinationEndpointErrorKind.NOT_WRITABLE,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.ERROR
        )
        assert (
            severity_for_error(
                VolumeErrorKind.UNLOCK_FAILED,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.ERROR
        )

    def test_ignore_none_everything_fatal(self) -> None:
        assert (
            severity_for_error(
                VolumeErrorKind.SENTINEL_NOT_FOUND,
                Strictness.IGNORE_NONE,
            )
            is Severity.ERROR
        )

    def test_disabled_sync_is_warning_under_every_strictness(self) -> None:
        """A disabled sync never fails a run, so it is never shown as an error."""
        assert {
            severity_for_error(SyncErrorKind.DISABLED, strictness)
            for strictness in Strictness
        } == {Severity.WARNING}

    def test_ignore_all_nothing_fatal(self) -> None:
        assert (
            severity_for_error(
                DestinationEndpointErrorKind.NOT_WRITABLE,
                Strictness.IGNORE_ALL,
            )
            is Severity.WARNING
        )

    def test_each_layer_classifier(self) -> None:
        """Sanity-check that each enum layer routes through correctly."""
        assert (
            severity_for_error(
                SshEndpointErrorKind.UNREACHABLE,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )
        assert (
            severity_for_error(
                SourceEndpointErrorKind.SENTINEL_NOT_FOUND,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )
        assert (
            severity_for_error(
                DestinationEndpointErrorKind.SENTINEL_NOT_FOUND,
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )
        assert (
            severity_for_error(
                SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
                Strictness.IGNORE_INACTIVE,
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
                    VolumeErrorKind.SENTINEL_NOT_FOUND,
                    VolumeErrorKind.DEVICE_NOT_PRESENT,
                ],
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.WARNING
        )

    def test_any_error_makes_it_error(self) -> None:
        assert (
            severity_for_errors(
                [
                    VolumeErrorKind.SENTINEL_NOT_FOUND,  # warning
                    VolumeErrorKind.UNLOCK_FAILED,  # error
                ],
                Strictness.IGNORE_INACTIVE,
            )
            is Severity.ERROR
        )

    def test_strictness_changes_classification(self) -> None:
        errors = [VolumeErrorKind.SENTINEL_NOT_FOUND]
        assert (
            severity_for_errors(errors, Strictness.IGNORE_INACTIVE) is Severity.WARNING
        )
        assert severity_for_errors(errors, Strictness.IGNORE_NONE) is Severity.ERROR
        assert severity_for_errors(errors, Strictness.IGNORE_ALL) is Severity.WARNING
