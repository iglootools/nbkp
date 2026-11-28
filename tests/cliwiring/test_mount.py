"""
Tests for nbkp.cliwiring.mount (managed_mount_or_exit with CLI reporting).

The volume ``enc`` is a LUKS drive found unlocked and mounted; on exit the
umount succeeds and the lock answers what each test scripts.
"""

import pytest
import typer

from nbkp.cliwiring.mount import lifecycle_session, managed_mount_or_exit
from nbkp.cliwiring.mount_progress import LifecycleProgress
from nbkp.cliwiring.output_format import OutputFormat
from nbkp.config import Config, CredentialProvider
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
            managed_mount_or_exit(
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
            managed_mount_or_exit(
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
        with managed_mount_or_exit(
            _CONFIG,
            {},
            runtime=runtime,
            writes=False,
            output_format=OutputFormat.JSON,
            umount_results=results,
        ):
            pass

        assert results == [LOCK_FAILED]

    def test_body_failure_reports_it_even_when_the_caller_owns_the_list(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        results: list[UmountResult] = []
        runtime = fake_runtime(run=mounted_drive_host(BUSY))
        with (
            pytest.raises(BodyError),
            managed_mount_or_exit(
                _CONFIG,
                {},
                runtime=runtime,
                writes=False,
                output_format=OutputFormat.JSON,
                umount_results=results,
            ),
        ):
            raise BodyError

        assert results == [LOCK_FAILED]
        assert "Umount failed: enc: lock failed (exit 1): busy" in (
            capsys.readouterr().err
        )

    def test_successful_umount_passes(self) -> None:
        run = mounted_drive_host(completed())
        with managed_mount_or_exit(
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


class TestPassphrasePrompt:
    def test_the_progress_bar_is_hidden_while_the_prompt_asks(self) -> None:
        config = _CONFIG.model_copy(
            update={"credential_provider": CredentialProvider.PROMPT},
        )
        sessions: list[LifecycleProgress] = []
        bar_shown_while_asking: list[bool] = []

        def prompt(_passphrase_id: str) -> str:
            [progress] = sessions
            assert progress.mount_bar is not None
            bar_shown_while_asking.append(progress.mount_bar.shown)
            return "typed-secret"

        with lifecycle_session(
            config,
            {},
            runtime=fake_runtime(prompt_passphrase=prompt),
            enabled=True,
        ) as (passphrase_fn, progress):
            sessions.append(progress)
            assert progress.mount_bar is not None
            # Unlocking happens with the mount bar on screen.
            progress.on_mount_start("enc")

            assert passphrase_fn("enc") == "typed-secret"

            assert bar_shown_while_asking == [False]
            assert progress.mount_bar.shown
