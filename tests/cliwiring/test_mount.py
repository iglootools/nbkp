"""Tests for nbkp.cliwiring.mount (managed_mount with CLI reporting).

The volume ``enc`` is a LUKS drive found unlocked and mounted; on exit the
umount succeeds and the lock answers what each test scripts.
"""

import subprocess

import pytest
import typer

from nbkp.clidisplay import OutputFormat
from nbkp.cliwiring.mount import managed_mount
from nbkp.config import Config, LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.disks import UmountResult
from nbkp.disks.models import LifecycleFailure, LifecycleStep, UmountFailureKind
from tests.fakes import FakeCommandRunner, completed, fake_runtime

UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"

LOCK_FAILED = UmountResult(
    "enc",
    success=False,
    failure_reason=UmountFailureKind.LOCK_FAILED,
    failure=LifecycleFailure(step=LifecycleStep.LOCK, exit_code=1, message="busy"),
)


def encrypted_volume(path: str = "/mnt/enc") -> LocalVolume:
    return LocalVolume(
        slug="enc",
        path=path,
        mount=MountConfig(
            device_uuid=UUID, encryption=LuksEncryptionConfig(passphrase_id="enc")
        ),
    )


def mounted_drive_host(
    lock: subprocess.CompletedProcess[str], *, path: str = "/mnt/enc"
) -> FakeCommandRunner:
    """A host where ``enc`` is plugged in, unlocked and mounted at *path*,
    and ``udisksctl lock`` answers *lock*."""
    return (
        FakeCommandRunner()
        .on("lsblk", returns=completed(stdout=f"sdb part\nluks-{UUID} crypt\n"))
        .on("findmnt", "--source", returns=completed(stdout=f"{path}\n"))
        .on("udisksctl", "lock", returns=lock)
    )


BUSY = completed(1, stderr="busy")

_CONFIG = Config(volumes={"enc": encrypted_volume()})


class TestUmountFailure:
    def test_fails_a_command_that_would_have_succeeded(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with (
            pytest.raises(typer.Exit) as excinfo,
            managed_mount(
                _CONFIG, {}, runtime=runtime, output_format=OutputFormat.JSON
            ),
        ):
            pass

        assert excinfo.value.exit_code == 1
        err = capsys.readouterr().err
        assert "Umount failed: enc: lock failed (exit 1): busy" in err
        assert "still unlocked" in err

    def test_body_failure_wins(self) -> None:
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with (
            pytest.raises(ValueError),
            managed_mount(
                _CONFIG, {}, runtime=runtime, output_format=OutputFormat.JSON
            ),
        ):
            raise ValueError("body failed")

    def test_handed_to_a_caller_that_reports_it(self) -> None:
        results: list[UmountResult] = []
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with managed_mount(
            _CONFIG,
            {},
            runtime=runtime,
            output_format=OutputFormat.JSON,
            umount_results=results,
        ):
            pass

        assert results == [LOCK_FAILED]

    def test_successful_umount_passes(self) -> None:
        run = mounted_drive_host(completed())
        with managed_mount(
            _CONFIG, {}, runtime=fake_runtime(run=run), output_format=OutputFormat.JSON
        ):
            pass

        assert [c[1] for c in run.commands_starting_with("udisksctl")] == [
            "unmount",
            "lock",
        ]
