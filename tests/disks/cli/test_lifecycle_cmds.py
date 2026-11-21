"""Tests for `disks mount` / `disks umount` / `disks setup-auth`.

The decisions behind the commands' outcome are tested from plain results;
the commands run against a host whose answers a FakeCommandRunner scripts
(``test -e``, ``lsblk``, ``findmnt``, ``udisksctl``), through an injected
runtime.  The LUKS volume ``enc`` takes its passphrase from the runtime's
environment.
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.console import Console

from nbkp.cli import app
from nbkp.config import Config, CredentialProvider
from nbkp.config.testkit import write_config
from nbkp.disks import MountResult, UmountResult, any_failed, refused_by_polkit
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountFailureKind,
)
from nbkp.disks.output import format_plan_line
from nbkp.disks.plan import MountAction, VolumePlan, plan_incomplete
from tests.clidisplay import runner
from tests.cliwiring.test_mount import UUID, encrypted_volume
from tests.fakes import FakeCommandRunner, completed, fake_runtime
from tests.preflight.fake_hosts import FAILS

_CLEARTEXT = f"/dev/mapper/luks-{UUID}"
_LOCKED = completed(stdout="sdb part\n")
_UNLOCKED = completed(stdout=f"sdb part\nluks-{UUID} crypt\n")
_MOUNTED = completed(stdout="/mnt/enc\n")
_NOT_MOUNTED = completed(1)


def _config() -> Config:
    return Config(
        credential_provider=CredentialProvider.ENV,
        volumes={"enc": encrypted_volume()},
    )


@pytest.fixture(autouse=True)
def _config_file(tmp_path: Path, chdir: Callable[[Path], None]) -> None:
    """Run every test from a directory holding ``_config()`` as ``config.yaml``.

    Commands get ``-c config.yaml``, and the hints they print show that
    same short relative path.
    """
    chdir(tmp_path)
    write_config(tmp_path, _config())


def _locked_drive() -> FakeCommandRunner:
    """``enc`` plugged in and locked; unlocking and mounting succeed."""
    return (
        FakeCommandRunner()
        .on("lsblk", returns=[_LOCKED, _UNLOCKED])
        .on("findmnt", "--source", returns=[_NOT_MOUNTED, _MOUNTED])
    )


def _mounted_drive(**udisks: subprocess.CompletedProcess[str]) -> FakeCommandRunner:
    """``enc`` unlocked and mounted until unmounted; the ``udisksctl``
    subcommands in *udisks* answer as given."""
    run = FakeCommandRunner()
    for step, answer in udisks.items():
        run.on("udisksctl", step, returns=answer)
    return run.on("lsblk", returns=_UNLOCKED).on(
        "findmnt", "--source", returns=[_MOUNTED, _NOT_MOUNTED]
    )


def _invoke(run: FakeCommandRunner, *args: str) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["disks", *args, "-c", "config.yaml"],
        obj=fake_runtime(run=run, environ={"NBKP_PASSPHRASE_ENC": "secret"}),
    )
    return result.exit_code, result.output


def _udisks_steps(run: FakeCommandRunner) -> list[str]:
    return [cmd[1] for cmd in run.commands_starting_with("udisksctl")]


def _failed_mount(
    reason: MountFailureKind, step: LifecycleStep, message: str
) -> MountResult:
    return MountResult(
        volume_slug="enc",
        success=False,
        failure_reason=reason,
        failure=LifecycleFailure(step=step, exit_code=1, message=message),
        device_present=True,
        luks_unlocked=False,
    )


class TestOutcomeDecisions:
    def test_any_failed(self) -> None:
        ok = UmountResult("enc", success=True)
        assert not any_failed([ok])
        assert any_failed([ok, UmountResult("usb", success=False)])

    def test_polkit_refusal_is_told_apart(self) -> None:
        refused = _failed_mount(
            MountFailureKind.NOT_AUTHORIZED, LifecycleStep.UNLOCK, "denied"
        )
        bad_key = _failed_mount(
            MountFailureKind.UNLOCK_FAILED, LifecycleStep.UNLOCK, "bad key"
        )
        assert refused_by_polkit([bad_key, refused])
        assert not refused_by_polkit([bad_key])


def _line(plan: VolumePlan) -> str:
    console = Console(width=200, color_system=None)
    with console.capture() as capture:
        console.print(format_plan_line(plan, plan.volume_slug))
    return capture.get().strip()


class TestPlanDecisions:
    def test_lines(self) -> None:
        assert (
            _line(VolumePlan("enc", (MountAction.UNMOUNT, MountAction.LOCK)))
            == "✓ would unmount, lock enc"
        )
        assert (
            _line(VolumePlan("enc", (), "device not plugged in"))
            == "✓ nothing to do for enc (device not plugged in)"
        )
        assert (
            _line(VolumePlan("enc", (), "unreachable: timed out", probe_failed=True))
            == "✗ cannot plan enc (unreachable: timed out)"
        )

    def test_a_volume_that_could_not_be_probed_makes_the_plan_incomplete(
        self,
    ) -> None:
        planned = VolumePlan("usb", (MountAction.MOUNT,))
        assert not plan_incomplete([planned])
        assert plan_incomplete(
            [planned, VolumePlan("enc", (), "unreachable", probe_failed=True)]
        )


class TestUnknownNames:
    def test_mount_rejects_unknown_name(self) -> None:
        run = FakeCommandRunner()

        exit_code, output = _invoke(run, "mount", "-n", "typo")

        assert exit_code == 2
        assert "unknown volume name(s): typo" in output
        assert run.calls == []

    def test_umount_rejects_unknown_name_as_json(self) -> None:
        run = FakeCommandRunner()

        exit_code, output = _invoke(run, "umount", "-n", "typo", "-o", "json")

        assert exit_code == 2
        error = json.loads(output)["error"]
        assert (error["reason"], error["names"]) == ("unknown-volume", ["typo"])
        assert run.calls == []


class TestMount:
    def test_unlocks_and_mounts(self) -> None:
        run = _locked_drive()

        exit_code, output = _invoke(run, "mount", "-o", "json")

        assert exit_code == 0, output
        [entry] = json.loads(output)
        assert (entry["luks_unlocked"], entry["mounted"]) == (True, True)
        assert _udisks_steps(run) == ["unlock", "mount"]
        [unlock] = [c for c in run.calls if c.cmd[:2] == ["udisksctl", "unlock"]]
        assert unlock.input == "secret"

    def test_failure_is_reported_in_json(self) -> None:
        run = _locked_drive().on(
            "udisksctl", "unlock", returns=completed(1, stderr="bad key")
        )

        exit_code, output = _invoke(run, "mount", "-o", "json")

        assert exit_code == 1
        [entry] = json.loads(output)
        assert entry == {
            "volume": "enc",
            "status": "probed",
            "device_present": True,
            "luks_unlocked": False,
            "mounted": None,
            "failure_reason": "unlock-failed",
            "detail": "unlock failed (exit 1): bad key",
            "failure": {"step": "unlock", "exit_code": 1, "message": "bad key"},
        }

    def test_polkit_refusal_suggests_setup_auth(self) -> None:
        run = _locked_drive().on(
            "udisksctl",
            "unlock",
            returns=completed(1, stderr="Not Authorized to perform operation"),
        )

        exit_code, output = _invoke(run, "mount")

        assert exit_code == 1
        assert "nbkp disks setup-auth -c config.yaml" in output

    def test_failure_suggests_troubleshoot(self) -> None:
        run = _locked_drive().on(
            "udisksctl", "mount", returns=completed(1, stderr="mount failed")
        )

        exit_code, output = _invoke(run, "mount", "-N", "private")

        assert exit_code == 1
        assert "nbkp preflight troubleshoot -c config.yaml -N private" in output


class TestUmount:
    def test_unmounts_and_locks(self) -> None:
        run = _mounted_drive()

        exit_code, output = _invoke(run, "umount", "-o", "json")

        assert exit_code == 0, output
        assert _udisks_steps(run) == ["unmount", "lock"]

    def test_failure_is_reported_in_json(self) -> None:
        run = _mounted_drive(lock=completed(1, stderr="busy"))

        exit_code, output = _invoke(run, "umount", "-o", "json")

        assert exit_code == 1
        [entry] = json.loads(output)
        assert (entry["luks_unlocked"], entry["mounted"]) == (True, False)
        assert entry["failure_reason"] == "lock-failed"
        assert entry["detail"] == "lock failed (exit 1): busy"
        assert entry["warning"] == "LUKS container still unlocked, manual lock needed"

    def test_failure_suggests_troubleshoot(self) -> None:
        run = _mounted_drive(unmount=completed(1, stderr="target is busy"))

        exit_code, output = _invoke(run, "umount", "-N", "private")

        assert exit_code == 1
        assert "nbkp preflight troubleshoot -c config.yaml -N private" in output


class TestDryRun:
    def test_mount_dry_run_plans_without_mounting(self) -> None:
        run = _locked_drive()

        exit_code, output = _invoke(run, "mount", "--dry-run", "-o", "json")

        assert exit_code == 0, output
        data = json.loads(output)
        assert data["dry_run"] is True
        assert data["volumes"] == [
            {
                "volume": "enc",
                "actions": ["unlock", "mount"],
                "detail": None,
                "probe_failed": False,
            }
        ]
        assert _udisks_steps(run) == []

    def test_umount_dry_run_plans_without_umounting(self) -> None:
        run = _mounted_drive()

        exit_code, output = _invoke(run, "umount", "--dry-run")

        assert exit_code == 0, output
        assert "would unmount, lock enc" in output
        assert _udisks_steps(run) == []

    def test_absent_drive_needs_nothing(self) -> None:
        exit_code, output = _invoke(
            FakeCommandRunner().on("test", "-e", returns=FAILS), "mount", "--dry-run"
        )

        assert exit_code == 0
        assert "nothing to do for enc" in output
        assert "device not plugged in" in output

    @pytest.mark.parametrize("command", ["mount", "umount"])
    def test_failed_probe_fails_the_dry_run(self, command: str) -> None:
        """A dry run that could not probe a volume must not report success."""
        unreachable = FakeCommandRunner().on("test", returns=OSError("timed out"))

        exit_code, output = _invoke(unreachable, command, "--dry-run", "-o", "json")

        assert exit_code == 1
        [plan] = json.loads(output)["volumes"]
        assert plan["probe_failed"] is True
        assert plan["detail"] == "unreachable: timed out"


class TestSetupAuth:
    def test_json(self) -> None:
        result = runner.invoke(
            app,
            ["disks", "setup-auth", "-c", "config.yaml", "-u", "backup", "-o", "json"],
        )
        assert result.exit_code == 0, result.output
        [rule] = json.loads(result.output)["rules"]
        assert rule["path"] == "/etc/polkit-1/rules.d/50-nbkp.rules"
        assert 'subject.user == "backup"' in rule["content"]

    def test_defaults_to_current_user(self) -> None:
        result = runner.invoke(
            app,
            ["disks", "setup-auth", "-c", "config.yaml"],
            obj=fake_runtime(local_user="alice"),
        )
        assert 'subject.user == "alice"' in result.output
