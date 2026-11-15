"""Tests for `disks mount` / `disks umount` / `disks setup-auth`."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import Config, LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.disks.lifecycle import MountResult, UmountResult
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountCapabilities,
    MountFailureReason,
    UmountFailureReason,
)
from nbkp.disks.plan import MountAction, VolumePlan

runner = CliRunner()

_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


def _config() -> Config:
    return Config(
        volumes={
            "enc": LocalVolume(
                slug="enc",
                path="/mnt/enc",
                mount=MountConfig(
                    device_uuid=_UUID,
                    encryption=LuksEncryptionConfig(passphrase_id="enc"),
                ),
            ),
        },
    )


class TestUnknownNames:
    @patch("nbkp.disks.cli.mount_cmd.mount_volumes")
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_mount_rejects_unknown_name(
        self, _load: MagicMock, mock_mount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "-n", "typo"])
        assert result.exit_code == 1
        assert "unknown volume name(s): typo" in result.output
        mock_mount.assert_not_called()

    @patch("nbkp.disks.cli.umount_cmd.umount_volumes")
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_umount_rejects_unknown_name_as_json(
        self, _load: MagicMock, mock_umount: MagicMock
    ) -> None:
        result = runner.invoke(
            app, ["disks", "umount", "-c", "/f.yaml", "-n", "typo", "-o", "json"]
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["names"] == ["typo"]
        mock_umount.assert_not_called()


class TestDryRun:
    @patch("nbkp.disks.cli.mount_cmd.mount_volumes")
    @patch(
        "nbkp.disks.cli.mount_cmd.plan_lifecycle",
        return_value=[VolumePlan("enc", (MountAction.UNLOCK, MountAction.MOUNT))],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_mount_dry_run_plans_without_mounting(
        self, _load: MagicMock, _probe: MagicMock, mock_mount: MagicMock
    ) -> None:
        result = runner.invoke(
            app, ["disks", "mount", "-c", "/f.yaml", "--dry-run", "-o", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["volumes"] == [
            {
                "volume": "enc",
                "actions": ["unlock", "mount"],
                "detail": None,
                "probe_failed": False,
            }
        ]
        mock_mount.assert_not_called()

    @patch("nbkp.disks.cli.umount_cmd.umount_volumes")
    @patch(
        "nbkp.disks.cli.umount_cmd.plan_lifecycle",
        return_value=[VolumePlan("enc", (MountAction.UNMOUNT, MountAction.LOCK))],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_umount_dry_run_plans_without_umounting(
        self, _load: MagicMock, _probe: MagicMock, mock_umount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "umount", "-c", "/f.yaml", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "would unmount, lock enc" in result.output
        mock_umount.assert_not_called()

    @patch(
        "nbkp.disks.cli.mount_cmd.plan_lifecycle",
        return_value=[VolumePlan("enc", (), "device not plugged in")],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_absent_drive_needs_nothing(
        self, _load: MagicMock, _probe: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "--dry-run"])
        assert "nothing to do for enc" in result.output
        assert "device not plugged in" in result.output

    @patch(
        "nbkp.disks.cli.mount_cmd.plan_lifecycle",
        return_value=[
            VolumePlan("enc", (), "unreachable: timed out", probe_failed=True)
        ],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_failed_probe_fails_the_dry_run(
        self, _load: MagicMock, _probe: MagicMock
    ) -> None:
        """A dry run that could not probe a volume must not report success."""
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "--dry-run"])
        assert result.exit_code == 1
        assert "cannot plan enc" in result.output
        assert "nothing to do" not in result.output

    @patch(
        "nbkp.disks.cli.umount_cmd.plan_lifecycle",
        return_value=[
            VolumePlan("enc", (), "unreachable: timed out", probe_failed=True)
        ],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_failed_probe_fails_the_json_dry_run(
        self, _load: MagicMock, _probe: MagicMock
    ) -> None:
        result = runner.invoke(
            app, ["disks", "umount", "-c", "/f.yaml", "--dry-run", "-o", "json"]
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["volumes"][0]["probe_failed"] is True


class TestMountAuthHint:
    @patch(
        "nbkp.disks.cli.mount_cmd.mount_volumes",
        return_value=[
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.NOT_AUTHORIZED,
                failure=LifecycleFailure(step=LifecycleStep.UNLOCK, exit_code=1),
                device_present=True,
                luks_unlocked=False,
            )
        ],
    )
    @patch("nbkp.disks.cli.mount_cmd.prefetch_passphrases", return_value=[])
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_not_authorized_suggests_setup_auth(
        self, _load: MagicMock, _prefetch: MagicMock, _mount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml"])
        assert result.exit_code == 1
        assert "nbkp disks setup-auth -c /f.yaml" in result.output


class TestFailureJson:
    @patch(
        "nbkp.disks.cli.mount_cmd.mount_volumes",
        return_value=[
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.UNLOCK_FAILED,
                failure=LifecycleFailure(
                    step=LifecycleStep.UNLOCK, exit_code=1, message="bad key"
                ),
                device_present=True,
                luks_unlocked=False,
            )
        ],
    )
    @patch("nbkp.disks.cli.mount_cmd.prefetch_passphrases", return_value=[])
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_mount_reports_why_it_failed(self, *_mocks: MagicMock) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "-o", "json"])

        assert result.exit_code == 1
        [entry] = json.loads(result.output)
        assert entry == {
            "volume": "enc",
            "device_present": True,
            "luks_unlocked": False,
            "mounted": None,
            "failure_reason": "unlock_failed",
            "detail": "unlock failed (exit 1): bad key",
            "failure": {"step": "unlock", "exit_code": 1, "message": "bad key"},
        }

    @patch(
        "nbkp.disks.cli.helpers.status.check_mount_status",
        return_value=MountCapabilities(
            device_present=True, luks_unlocked=True, mounted=False
        ),
    )
    @patch(
        "nbkp.disks.cli.umount_cmd.umount_volumes",
        return_value=[
            UmountResult(
                volume_slug="enc",
                success=False,
                failure_reason=UmountFailureReason.LOCK_FAILED,
                failure=LifecycleFailure(
                    step=LifecycleStep.LOCK, exit_code=1, message="busy"
                ),
            )
        ],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_umount_reports_why_it_failed(self, *_mocks: MagicMock) -> None:
        result = runner.invoke(app, ["disks", "umount", "-c", "/f.yaml", "-o", "json"])

        assert result.exit_code == 1
        [entry] = json.loads(result.output)
        assert entry["luks_unlocked"] is True
        assert entry["failure_reason"] == "lock_failed"
        assert entry["detail"] == "lock failed (exit 1): busy"
        assert entry["warning"] == "LUKS container still unlocked, manual lock needed"


class TestTroubleshootHint:
    """A failed mount or umount points at ``preflight troubleshoot``."""

    @patch(
        "nbkp.disks.cli.mount_cmd.mount_volumes",
        return_value=[
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.MOUNT_FAILED,
                failure=LifecycleFailure(
                    step=LifecycleStep.MOUNT, exit_code=1, message="mount failed"
                ),
                device_present=True,
                luks_unlocked=True,
            )
        ],
    )
    @patch("nbkp.disks.cli.mount_cmd.prefetch_passphrases", return_value=[])
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_mount_failure_suggests_troubleshoot(
        self, _load: MagicMock, _prefetch: MagicMock, _mount: MagicMock
    ) -> None:
        result = runner.invoke(
            app, ["disks", "mount", "-c", "/f.yaml", "-N", "private"]
        )
        assert result.exit_code == 1
        assert "nbkp preflight troubleshoot -c /f.yaml -N private" in result.output

    @patch(
        "nbkp.disks.cli.helpers.status.check_mount_status",
        return_value=MountCapabilities(
            device_present=True, luks_unlocked=True, mounted=True
        ),
    )
    @patch(
        "nbkp.disks.cli.umount_cmd.umount_volumes",
        return_value=[
            UmountResult(
                volume_slug="enc",
                success=False,
                failure_reason=UmountFailureReason.UNMOUNT_FAILED,
                failure=LifecycleFailure(
                    step=LifecycleStep.UNMOUNT, exit_code=1, message="target is busy"
                ),
            )
        ],
    )
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_umount_failure_suggests_troubleshoot(
        self, _load: MagicMock, _umount: MagicMock, _probe: MagicMock
    ) -> None:
        result = runner.invoke(
            app, ["disks", "umount", "-c", "/f.yaml", "-N", "private"]
        )
        assert result.exit_code == 1
        assert "nbkp preflight troubleshoot -c /f.yaml -N private" in result.output


class TestSetupAuth:
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_json(self, _load: MagicMock) -> None:
        result = runner.invoke(
            app,
            ["disks", "setup-auth", "-c", "/f.yaml", "-u", "backup", "-o", "json"],
        )
        assert result.exit_code == 0, result.output
        [rule] = json.loads(result.output)["rules"]
        assert rule["path"] == "/etc/polkit-1/rules.d/50-nbkp.rules"
        assert 'subject.user == "backup"' in rule["content"]

    @patch("nbkp.disks.cli.setup_auth_cmd.getpass.getuser", return_value="alice")
    @patch("nbkp.cliwiring.config.load_config", return_value=_config())
    def test_defaults_to_current_user(self, _load: MagicMock, _user: MagicMock) -> None:
        result = runner.invoke(app, ["disks", "setup-auth", "-c", "/f.yaml"])
        assert 'subject.user == "alice"' in result.output
