"""Tests for `disks mount` / `disks umount` / `disks setup-auth`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import Config, LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.disks.lifecycle import MountResult
from nbkp.disks.models import MountCapabilities, MountFailureReason

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
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
    def test_mount_rejects_unknown_name(
        self, _load: MagicMock, mock_mount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "-n", "typo"])
        assert result.exit_code == 1
        assert "unknown volume name(s): typo" in result.output
        mock_mount.assert_not_called()

    @patch("nbkp.disks.cli.umount_cmd.umount_volumes")
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
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
        "nbkp.disks.plan.check_mount_status",
        return_value=MountCapabilities(
            device_present=True, luks_unlocked=False, mounted=False
        ),
    )
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
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
        "nbkp.disks.plan.check_mount_status",
        return_value=MountCapabilities(
            device_present=True, luks_unlocked=True, mounted=True
        ),
    )
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
    def test_umount_dry_run_plans_without_umounting(
        self, _load: MagicMock, _probe: MagicMock, mock_umount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "umount", "-c", "/f.yaml", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "would unmount, lock enc" in result.output
        mock_umount.assert_not_called()

    @patch(
        "nbkp.disks.plan.check_mount_status",
        return_value=MountCapabilities(device_present=False),
    )
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
    def test_absent_drive_needs_nothing(
        self, _load: MagicMock, _probe: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml", "--dry-run"])
        assert "nothing to do for enc" in result.output
        assert "device not plugged in" in result.output


class TestMountAuthHint:
    @patch(
        "nbkp.disks.cli.mount_cmd.mount_volumes",
        return_value=[
            MountResult(
                volume_slug="enc",
                success=False,
                detail="unlock not authorized — polkit rule not configured",
                failure_reason=MountFailureReason.NOT_AUTHORIZED,
                device_present=True,
                luks_unlocked=False,
            )
        ],
    )
    @patch("nbkp.disks.cli.mount_cmd.prefetch_passphrases", return_value=[])
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
    def test_not_authorized_suggests_setup_auth(
        self, _load: MagicMock, _prefetch: MagicMock, _mount: MagicMock
    ) -> None:
        result = runner.invoke(app, ["disks", "mount", "-c", "/f.yaml"])
        assert result.exit_code == 1
        assert "nbkp disks setup-auth -c /f.yaml" in result.output


class TestSetupAuth:
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
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
    @patch("nbkp.config.cli.helpers.load_config", return_value=_config())
    def test_defaults_to_current_user(self, _load: MagicMock, _user: MagicMock) -> None:
        result = runner.invoke(app, ["disks", "setup-auth", "-c", "/f.yaml"])
        assert 'subject.user == "alice"' in result.output
