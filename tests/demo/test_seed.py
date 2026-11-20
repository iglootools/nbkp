"""Tests for `nbkp demo seed` helpers that need no Docker."""

import shlex
import subprocess
from pathlib import Path

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
from nbkp.demo import seed as seed_module
from nbkp.demo.cli import app
from nbkp.demo.cli.seed_cmd import commands_script, format_seed_error
from nbkp.demo.seed import (
    SeedError,
    SeedErrorKind,
    SeedResult,
    seed_demo,
    seed_plan,
    seed_steps,
)
from nbkp.remote.testkit.constants import LUKS_PASSPHRASE
from tests.cliwiring.test_mount import UUID, encrypted_volume
from tests.fakes import FakeCommandRunner, completed


class TestDryRun:
    def test_plan_lists_docker_steps(self) -> None:
        steps = seed_plan(Path("/tmp/x"), docker=True, luks=True)
        assert steps[0] == "create seed directory /tmp/x"
        assert "start the bastion and storage containers" in steps
        assert "mount, seed and umount the encrypted volume" in steps

    def test_cli_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "seed"
        result = CliRunner().invoke(
            app, ["seed", "--dry-run", "--no-docker", "--base-dir", str(target)]
        )
        assert result.exit_code == 0, result.output
        assert "nothing created" in result.output
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
        self, tmp_path: Path
    ) -> None:
        """Regression: the config had no credential-command and failed
        validation after the containers had started."""
        endpoint = SshEndpoint(slug="bastion", host="127.0.0.1")
        containers = seed_module._Containers(
            tmp_path / "id", endpoint, endpoint, 2222, 2223
        )
        config = seed_module._docker_config(
            tmp_path, containers, UUID, RsyncOptions(), CredentialProvider.COMMAND
        )
        assert config.credential_provider == CredentialProvider.COMMAND
        passphrase = retrieve_passphrase(
            "test-luks", config.credential_provider, config.credential_command
        )
        assert passphrase == LUKS_PASSPHRASE


class TestDockerExtra:
    def test_missing_extra_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(SeedError) as exc:
            seed_demo(tmp_path, docker=True, has_docker=False)
        assert exc.value.kind == SeedErrorKind.DOCKER_EXTRA_MISSING
        assert not exc.value.may_leave_containers
        assert "nbkp[docker]" in format_seed_error(exc.value)


_ENC = Config(volumes={"enc": encrypted_volume()})


def _drive(
    *, mounts: bool = True, **udisks: subprocess.CompletedProcess[str]
) -> FakeCommandRunner:
    """``enc`` plugged in and locked, unlocked once unlocked, and mounted once
    mounted if it *mounts*; the ``udisksctl`` subcommands in *udisks* answer
    as given."""
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
            seed_module._encrypted_volume_mounted(
                _ENC,
                {},
                True,
                seed_module._Steps(
                    None, lambda label, sev, _d: ends.append((label, sev))
                ),
                passphrase_fn=lambda _: "x",
                run=run,
            ),
        ):
            pass
        assert exc.value.kind == SeedErrorKind.UMOUNT_FAILED
        assert exc.value.detail == "umount failed (exit 1): busy"
        assert ends[-1] == ("umount encrypted volume", "error")

    def test_failed_mount_still_locks(self) -> None:
        """A mount that fails after unlocking must not leave LUKS unlocked."""
        run = _drive(mounts=False, mount=completed(1, stderr="no fs"))
        with (
            pytest.raises(SeedError) as exc,
            seed_module._encrypted_volume_mounted(
                _ENC,
                {},
                True,
                seed_module._Steps(None, None),
                passphrase_fn=lambda _: "x",
                run=run,
            ),
        ):
            pass
        assert exc.value.kind == SeedErrorKind.MOUNT_FAILED
        assert "no fs" in exc.value.detail
        assert [c[1] for c in run.commands_starting_with("udisksctl")] == [
            "unlock",
            "mount",
            "lock",
        ]


class TestStepFailure:
    def test_a_raising_step_is_reported_failed(self) -> None:
        ends: list[tuple[str, str, str | None]] = []
        steps = seed_module._Steps(None, lambda *end: ends.append(end))
        step = seed_steps(docker=True, luks=False)[0]
        with pytest.raises(TimeoutError), steps.run(step):
            raise TimeoutError("SSH not ready after 30s")
        assert ends == [(step.done, "error", "SSH not ready after 30s")]


class TestSeedErrorRendering:
    @pytest.mark.parametrize(
        "kind",
        sorted(
            set(SeedErrorKind)
            - {SeedErrorKind.DOCKER_EXTRA_MISSING, SeedErrorKind.LUKS_UNAVAILABLE}
        ),
    )
    def test_a_kind_with_a_detail_renders_it(self, kind: SeedErrorKind) -> None:
        assert "DETAIL" in format_seed_error(SeedError(kind, "DETAIL"))

    def test_a_failure_after_the_prerequisites_may_leave_containers(self) -> None:
        assert SeedError(SeedErrorKind.SSH_NOT_READY, "x").may_leave_containers
        assert not SeedError(SeedErrorKind.DOCKER_UNAVAILABLE, "x").may_leave_containers


class TestCommandsScript:
    def test_paths_are_quoted(self) -> None:
        base = Path("/tmp/my demo")
        result = SeedResult(
            base_dir=base,
            config_path=base / "config.yaml",
            config=Config(volumes={"v": LocalVolume(slug="v", path="/v")}),
        )
        script = commands_script(result, False, False, CredentialProvider.KEYRING)
        assert f"CFG={shlex.quote('/tmp/my demo/config.yaml')}" in script
        assert 'nbkp run --config "$CFG"' in script
        assert " $CFG" not in script
        # No stray indentation from splicing into a dedent template.
        assert not any(line.startswith(" " * 4 + "#") for line in script.splitlines())
