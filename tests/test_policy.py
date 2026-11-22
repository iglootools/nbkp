"""Tests for the shared severity classifier."""

from nbkp.policy import Severity, Strictness, classify_severity


class TestClassifySeverity:
    def test_ignore_none_inactive_is_error(self) -> None:
        assert (
            classify_severity(is_inactive=True, strictness=Strictness.IGNORE_NONE)
            is Severity.ERROR
        )

    def test_ignore_none_active_is_error(self) -> None:
        assert (
            classify_severity(is_inactive=False, strictness=Strictness.IGNORE_NONE)
            is Severity.ERROR
        )

    def test_ignore_all_inactive_is_warning(self) -> None:
        assert (
            classify_severity(is_inactive=True, strictness=Strictness.IGNORE_ALL)
            is Severity.WARNING
        )

    def test_ignore_all_active_is_warning(self) -> None:
        assert (
            classify_severity(is_inactive=False, strictness=Strictness.IGNORE_ALL)
            is Severity.WARNING
        )

    def test_ignore_inactive_inactive_is_warning(self) -> None:
        assert (
            classify_severity(is_inactive=True, strictness=Strictness.IGNORE_INACTIVE)
            is Severity.WARNING
        )

    def test_ignore_inactive_active_is_error(self) -> None:
        assert (
            classify_severity(is_inactive=False, strictness=Strictness.IGNORE_INACTIVE)
            is Severity.ERROR
        )
