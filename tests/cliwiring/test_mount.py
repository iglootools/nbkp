"""
Tests for nbkp.cliwiring.mount (managed_mount with CLI reporting).

The volume ``enc`` is a LUKS drive found unlocked and mounted; on exit the
umount succeeds and the lock answers what each test scripts.
"""

import pytest
import typer

from nbkp.clidisplay import OutputFormat
from nbkp.cliwiring.mount import managed_mount
from nbkp.config import Config
from nbkp.disks import UmountResult
from tests.fakes import BodyError, completed, fake_runtime
from tests.fixtures.disks import BUSY, LOCK_FAILED, encrypted_volume, mounted_drive_host

_CONFIG = Config(volumes={"enc": encrypted_volume()})


class TestUmountFailure:
    def test_fails_a_command_that_would_have_succeeded(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with (
            pytest.raises(typer.Exit) as excinfo,
            managed_mount(
                _CONFIG,
                {},
                runtime=runtime,
                writes=False,
                output_format=OutputFormat.JSON,
            ),
        ):
            pass

        assert excinfo.value.exit_code == 1
        err = capsys.readouterr().err
        assert "Umount failed: enc: lock failed (exit 1): busy" in err
        assert "still unlocked" in err

    def test_body_failure_wins_and_the_umount_failure_is_still_reported(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with (
            pytest.raises(BodyError),
            managed_mount(
                _CONFIG,
                {},
                runtime=runtime,
                writes=False,
                output_format=OutputFormat.JSON,
            ),
        ):
            raise BodyError

        assert "Umount failed: enc: lock failed (exit 1): busy" in (
            capsys.readouterr().err
        )

    def test_handed_to_a_caller_that_reports_it(self) -> None:
        results: list[UmountResult] = []
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with managed_mount(
            _CONFIG,
            {},
            runtime=runtime,
            writes=False,
            output_format=OutputFormat.JSON,
            umount_results=results,
        ):
            pass

        assert results == [LOCK_FAILED]

    def test_successful_umount_passes(self) -> None:
        run = mounted_drive_host(completed())
        with managed_mount(
            _CONFIG,
            {},
            runtime=fake_runtime(run=run),
            writes=False,
            output_format=OutputFormat.JSON,
        ):
            pass

        assert [c[1] for c in run.commands_starting_with("udisksctl")] == [
            "unmount",
            "lock",
        ]
