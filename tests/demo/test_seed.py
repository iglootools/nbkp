"""Tests for `nbkp demo seed` helpers that need no Docker."""

from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nbkp.config import Config, CredentialProvider, LocalVolume
from nbkp.demo.cli import app
from nbkp.demo.cli.cmd_handler import seed as seed_module
from nbkp.demo.cli.cmd_handler.seed import SeedError, SeedResult, seed_demo, seed_plan
from nbkp.demo.cli.seed_cmd import commands_script
from nbkp.disks.lifecycle import UmountResult


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
    def test_missing_extra_is_a_clear_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(seed_module, "_HAS_DOCKER", False)
        with pytest.raises(SeedError, match=r"nbkp\[docker\]"):
            seed_demo(tmp_path, docker=True)


class TestUmountFailure:
    def test_failed_umount_is_reported(self, tmp_path: Path) -> None:
        """Regression: the umount result was ignored, leaving LUKS unlocked."""
        ends: list[tuple[str, str]] = []
        with (
            patch.object(seed_module, "mount_volumes", return_value=[]),
            patch.object(
                seed_module,
                "umount_volumes",
                return_value=[UmountResult("enc", success=False, detail="busy")],
            ),
            patch.object(seed_module, "LUKS_PASSPHRASE", "x", create=True),
            pytest.raises(SeedError, match="Umount failed: busy"),
            seed_module._encrypted_volume_mounted(
                Config(),
                {},
                True,
                seed_module._Steps(
                    None, lambda label, sev, _d: ends.append((label, sev))
                ),
            ),
        ):
            pass
        assert ends[-1] == ("umount encrypted volume", "error")


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
