"""Tests for `nbkp demo seed` helpers that need no Docker."""

import shlex
import subprocess
from pathlib import Path

import paramiko
import pytest
from typer.testing import CliRunner

from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    RsyncOptions,
    SshEndpoint,
)
from nbkp.credentials import retrieve_passphrase
from nbkp.demo.cli import app
from nbkp.demo.cli.seed_cmd import commands_script, format_seed_error
from nbkp.demo.seed import (
    SeedError,
    SeedErrorKind,
    SeedResult,
    as_seed_errors,
    docker_demo_config,
    encrypted_volume_mounted,
    seed_demo,
    seed_plan,
    seed_steps,
)
from nbkp.disks import MountResult, UmountFailureKind, UmountResult
from nbkp.remote.resolution import SshConfigError, SshConfigErrorKind
from nbkp.remote.testkit.constants import LUKS_PASSPHRASE
from tests.fakes import FakeCommandRunner, completed
from tests.fixtures.disks import UUID, encrypted_volume


class TestDryRun:
    def test_plan_lists_docker_steps(self) -> None:
        steps = seed_plan(Path("/tmp/x"), docker=True, luks=True)  # noqa: S108 -- a plan only, never written
        assert steps[0] == "create seed directory /tmp/x"
        assert "start the storage container" in steps
        assert steps.index("mount the encrypted volume") < steps.index(
            "create sentinels and seed test data",
        )
        assert steps[-2:] == ["umount the encrypted volume", "write config.yaml"]

    def test_local_plan_has_no_docker_step(self) -> None:
        assert seed_plan(None, docker=False, luks=True) == [
            "create seed directory a new temp dir (nbkp-demo-*)",
            "create sentinels and seed test data",
            "write config.yaml",
        ]

    def test_cli_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "seed"
        result = CliRunner().invoke(
            app,
            ["seed", "--dry-run", "--no-docker", "--base-dir", str(target)],
        )
        assert result.exit_code == 0, result.output
        assert result.output.startswith("[dry-run] Would create seed directory")
        assert not target.exists()


class TestSteps:
    def test_local_seed_reports_the_listed_steps(self, tmp_path: Path) -> None:
        ends: list[str] = []
        seed_demo(
            tmp_path,
            docker=False,
            big_file_size=0,
            on_step_end=lambda label, _sev, _detail: ends.append(label),
        )
        assert ends == [s.done for s in seed_steps(docker=False, luks=False)]


class TestCredentialProvider:
    def test_command_provider_config_retrieves_the_demo_passphrase(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Regression: the demo config lacked a credential-command.

        It failed validation after the containers had started.
        """
        config = docker_demo_config(
            tmp_path,
            bastion=SshEndpoint(slug="bastion", host="127.0.0.1"),
            private_key=tmp_path / "id",
            luks_uuid=UUID,
            rsync_options=RsyncOptions(),
            credential_provider=CredentialProvider.COMMAND,
        )
        assert config.credential_provider == CredentialProvider.COMMAND
        passphrase = retrieve_passphrase(
            "test-luks",
            config.credential_provider,
            config.credential_command,
        )
        assert passphrase == LUKS_PASSPHRASE


class TestDockerExtra:
    def test_missing_extra_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(SeedError) as exc:
            seed_demo(tmp_path, docker=True, has_docker=False)
        assert exc.value.kind == SeedErrorKind.DOCKER_EXTRA_MISSING
        assert not exc.value.may_leave_containers
        assert "nbkp[docker]" in format_seed_error(exc.value)

    def test_reseeding_a_docker_seed_dir_fails_before_any_container(
        self,
        tmp_path: Path,
    ) -> None:
        """Regression: the key's exclusive mkdir failed after the checks passed."""
        (tmp_path / "ssh").mkdir()
        with pytest.raises(SeedError) as exc:
            seed_demo(tmp_path, docker=True, has_docker=True)
        assert exc.value.kind == SeedErrorKind.SEED_DIR_REUSED
        assert exc.value.detail == str(tmp_path / "ssh")
        assert not exc.value.may_leave_containers


_ENC = Config(volumes={"enc": encrypted_volume()})


def _drive(
    *,
    mounts: bool = True,
    **udisks: subprocess.CompletedProcess[str],
) -> FakeCommandRunner:
    """
    Script a host where ``enc`` is plugged in and locked.

    Unlocked once unlocked, and mounted once mounted if it *mounts*; the ``udisksctl``
    subcommands in *udisks* answer as given.
    """
    run = FakeCommandRunner()
    for step, answer in udisks.items():
        run.on("udisksctl", step, returns=answer)
    return run.on(
        "lsblk",
        returns=[
            completed(stdout="sdb part\n"),
            completed(stdout=f"sdb part\nluks-{UUID} crypt\n"),
        ],
    ).on(
        "findmnt",
        "--source",
        returns=[
            completed(1),
            completed(stdout="/mnt/enc\n") if mounts else completed(1),
        ],
    )


class TestUmountFailure:
    def test_failed_umount_is_reported(self) -> None:
        """Regression: the umount result was ignored, leaving LUKS unlocked."""
        ends: list[tuple[str, str]] = []
        run = _drive(unmount=completed(1, stderr="busy"))
        with (
            pytest.raises(SeedError) as exc,
            encrypted_volume_mounted(
                _ENC,
                {},
                passphrase_fn=lambda _: "x",
                run=run,
                on_step_end=lambda label, sev, _d: ends.append((label, sev)),
            ),
        ):
            pass
        assert exc.value.kind == SeedErrorKind.UMOUNT_FAILED
        assert isinstance(exc.value.result, UmountResult)
        assert exc.value.result.failure_reason == UmountFailureKind.UNMOUNT_FAILED
        assert "umount failed (exit 1): busy" in format_seed_error(exc.value)
        assert ends[-1] == ("umount encrypted volume", "error")

    def test_failed_mount_still_locks(self) -> None:
        """A mount that fails after unlocking must not leave LUKS unlocked."""
        run = _drive(mounts=False, mount=completed(1, stderr="no fs"))
        with (
            pytest.raises(SeedError) as exc,
            encrypted_volume_mounted(
                _ENC,
                {},
                passphrase_fn=lambda _: "x",
                run=run,
            ),
        ):
            pass
        assert exc.value.kind == SeedErrorKind.MOUNT_FAILED
        assert isinstance(exc.value.result, MountResult)
        assert "mount failed (exit 1): no fs" in format_seed_error(exc.value)
        assert [c[1] for c in run.commands_starting_with("udisksctl")] == [
            "unlock",
            "mount",
            "lock",
        ]


class TestStepFailure:
    def test_a_failed_mount_is_reported_with_its_detail(self) -> None:
        ends: list[tuple[str, str, str | None]] = []
        run = _drive(mounts=False, mount=completed(1, stderr="no fs"))
        with (
            pytest.raises(SeedError),
            encrypted_volume_mounted(
                _ENC,
                {},
                passphrase_fn=lambda _: "x",
                run=run,
                on_step_end=lambda step, severity, detail: ends.append(
                    (step, severity, detail),
                ),
            ),
        ):
            pass
        assert ends[0] == (
            "mount encrypted volume",
            "error",
            "no fs",
        )

    def test_a_raising_step_is_reported_failed(self, tmp_path: Path) -> None:
        # A file where the seeding creates the source volume's directory.
        (tmp_path / "src-local-bare").write_text("")
        ends: list[tuple[str, str, str | None]] = []
        with pytest.raises(SeedError) as exc:
            seed_demo(
                tmp_path,
                docker=False,
                big_file_size=0,
                on_step_end=lambda step, severity, detail: ends.append(
                    (step, severity, detail),
                ),
            )
        assert ends == [("seed volumes", "error", exc.value.detail)]

    def test_a_file_error_is_a_seed_error(self, tmp_path: Path) -> None:
        """Regression: an OSError escaped seed_demo as a traceback."""
        base_dir = tmp_path / "seed"
        base_dir.write_text("")  # A file where the seed directory goes.
        with pytest.raises(SeedError) as exc:
            seed_demo(base_dir, docker=False, big_file_size=0)
        assert exc.value.kind == SeedErrorKind.FILE_OPERATION_FAILED
        assert str(base_dir) in format_seed_error(exc.value)

    def test_cli_prints_the_summary_and_commands_after_a_successful_seed(
        self,
        tmp_path: Path,
    ) -> None:
        result = CliRunner().invoke(
            app,
            [
                "seed",
                "--no-docker",
                "--big-file-size",
                "0",
                "--base-dir",
                str(tmp_path),
            ],
            env={"COLUMNS": "400"},
        )

        assert result.exit_code == 0, result.output
        assert str(tmp_path / "config.yaml") in result.stdout
        assert "CFG=" in result.stdout

    def test_cli_reports_a_file_error_without_a_traceback(
        self,
        tmp_path: Path,
    ) -> None:
        # A file where the seeding creates the source volume's directory.
        (tmp_path / "src-local-bare").write_text("")
        result = CliRunner().invoke(
            app,
            [
                "seed",
                "--no-docker",
                "--big-file-size",
                "0",
                "--base-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1, result.output
        assert not isinstance(result.exception, OSError)
        assert "Writing the demo files failed" in result.stderr
        assert "Writing the demo files failed" not in result.stdout


class TestAsSeedErrors:
    @pytest.mark.parametrize(
        ("error", "kind"),
        [
            (paramiko.SSHException("No existing session"), SeedErrorKind.SSH_FAILED),
            (EOFError(), SeedErrorKind.SSH_FAILED),
            (
                SshConfigError(SshConfigErrorKind.INVALID_PORT, "nas", "abc"),
                SeedErrorKind.SSH_CONFIG_INVALID,
            ),
            (TimeoutError("no banner"), SeedErrorKind.SSH_NOT_READY),
            (PermissionError("denied"), SeedErrorKind.FILE_OPERATION_FAILED),
        ],
    )
    def test_step_failure_becomes_a_seed_error(
        self,
        error: Exception,
        kind: SeedErrorKind,
    ) -> None:
        """Regression: SSH and SSH-config errors escaped as tracebacks."""
        with pytest.raises(SeedError) as exc, as_seed_errors():
            raise error
        assert exc.value.kind == kind
        assert exc.value.__cause__ is error


class TestSeedErrorRendering:
    @pytest.mark.parametrize(
        "kind",
        sorted(
            set(SeedErrorKind)
            - {SeedErrorKind.DOCKER_EXTRA_MISSING, SeedErrorKind.LUKS_UNAVAILABLE},
        ),
    )
    def test_a_kind_with_a_detail_renders_it(self, kind: SeedErrorKind) -> None:
        assert "DETAIL" in format_seed_error(SeedError(kind, "DETAIL"))

    def test_a_failure_after_the_prerequisites_may_leave_containers(self) -> None:
        assert SeedError(SeedErrorKind.SSH_NOT_READY, "x").may_leave_containers
        assert not SeedError(SeedErrorKind.DOCKER_UNAVAILABLE, "x").may_leave_containers


class TestCommandsScript:
    def test_paths_are_quoted(self) -> None:
        base = Path("/tmp/my demo")  # noqa: S108 -- data only, never written
        result = SeedResult(
            base_dir=base,
            config_path=base / "config.yaml",
            config=Config(volumes={"v": LocalVolume(slug="v", path="/v")}),
        )
        script = commands_script(
            result,
            docker=False,
            has_luks=False,
            credential_provider=CredentialProvider.KEYRING,
        )
        assert f"CFG={shlex.quote('/tmp/my demo/config.yaml')}" in script  # noqa: S108 -- data only, never written
        assert 'nbkp run --config "$CFG"' in script
        assert " $CFG" not in script
        # No stray indentation from splicing into a dedent template.
        assert not any(line.startswith(" " * 4 + "#") for line in script.splitlines())
