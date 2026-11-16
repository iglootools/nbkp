"""Tests for `nbkp demo seed` helpers that need no Docker."""

import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nbkp.config import Config, CredentialProvider, LocalVolume
from nbkp.demo.cli import app
from nbkp.demo.cli.cmd_handler import seed as seed_module
from nbkp.demo.cli.cmd_handler.seed import SeedError, SeedResult, seed_demo, seed_plan
from nbkp.demo.cli.seed_cmd import commands_script
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


class TestDockerExtra:
    def test_missing_extra_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(SeedError, match=r"nbkp\[docker\]"):
            seed_demo(tmp_path, docker=True, has_docker=False)


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
            pytest.raises(
                SeedError, match=r"Umount failed: umount failed \(exit 1\): busy"
            ),
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
        assert ends[-1] == ("umount encrypted volume", "error")

    def test_failed_mount_still_locks(self) -> None:
        """A mount that fails after unlocking must not leave LUKS unlocked."""
        run = _drive(mounts=False, mount=completed(1, stderr="no fs"))
        with (
            pytest.raises(SeedError, match="Mount failed: .*no fs"),
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
        assert [c[1] for c in run.commands_starting_with("udisksctl")] == [
            "unlock",
            "mount",
            "lock",
        ]


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
