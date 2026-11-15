"""Tests for nbkp.cliwiring.mount (managed_mount with CLI reporting)."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
import typer

from nbkp.clidisplay import OutputFormat
from nbkp.cliwiring.mount import managed_mount
from nbkp.config import Config
from nbkp.disks import UmountResult
from nbkp.disks.models import LifecycleFailure, LifecycleStep, UmountFailureReason

LOCK_FAILED = UmountResult(
    "enc",
    success=False,
    failure_reason=UmountFailureReason.LOCK_FAILED,
    failure=LifecycleFailure(step=LifecycleStep.LOCK, exit_code=1, message="busy"),
)


def fake_disks_managed_mount(result: UmountResult) -> Callable[..., Any]:
    """Stands in for ``nbkp.disks.managed_mount``: umounts *result* on exit."""

    @contextmanager
    def fake(
        cfg: Config,
        *_args: object,
        on_umount_end: Callable[[str, UmountResult], None],
        **_kwargs: object,
    ) -> Generator[tuple[Config, dict[str, object]]]:
        try:
            yield cfg, {}
        finally:
            on_umount_end(result.volume_slug, result)

    return fake


class TestUmountFailure:
    def test_fails_a_command_that_would_have_succeeded(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch(
                "nbkp.cliwiring.mount._disks_managed_mount",
                fake_disks_managed_mount(LOCK_FAILED),
            ),
            pytest.raises(typer.Exit) as excinfo,
            managed_mount(Config(), {}, output_format=OutputFormat.JSON),
        ):
            pass

        assert excinfo.value.exit_code == 1
        err = capsys.readouterr().err
        assert "Umount failed: enc: lock failed (exit 1): busy" in err
        assert "still unlocked" in err

    def test_body_failure_wins(self) -> None:
        with (
            patch(
                "nbkp.cliwiring.mount._disks_managed_mount",
                fake_disks_managed_mount(LOCK_FAILED),
            ),
            pytest.raises(ValueError),
            managed_mount(Config(), {}, output_format=OutputFormat.JSON),
        ):
            raise ValueError("body failed")

    def test_handed_to_a_caller_that_reports_it(self) -> None:
        results: list[UmountResult] = []
        with (
            patch(
                "nbkp.cliwiring.mount._disks_managed_mount",
                fake_disks_managed_mount(LOCK_FAILED),
            ),
            managed_mount(
                Config(), {}, output_format=OutputFormat.JSON, umount_results=results
            ),
        ):
            pass

        assert results == [LOCK_FAILED]

    def test_successful_umount_passes(self) -> None:
        with (
            patch(
                "nbkp.cliwiring.mount._disks_managed_mount",
                fake_disks_managed_mount(UmountResult("enc", success=True)),
            ),
            managed_mount(Config(), {}, output_format=OutputFormat.JSON),
        ):
            pass
