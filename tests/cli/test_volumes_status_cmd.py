"""Tests for the `volumes status` CLI command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.preflight import MountCapabilities

runner = CliRunner()


def _mount_config_for_status() -> Config:
    """Config with volumes that have mount config."""
    return Config(
        ssh_endpoints={},
        volumes={
            "encrypted-drive": LocalVolume(
                slug="encrypted-drive",
                path="/mnt/encrypted",
                mount=MountConfig(
                    device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                    encryption=LuksEncryptionConfig(
                        mapper_name="encrypted",
                        passphrase_id="encrypted",
                    ),
                ),
            ),
            "plain-drive": LocalVolume(
                slug="plain-drive",
                path="/mnt/plain",
                mount=MountConfig(
                    device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                ),
            ),
            "no-mount": LocalVolume(slug="no-mount", path="/home/user"),
        },
        sync_endpoints={},
        syncs={},
    )


class TestVolumesStatusCommand:
    @patch("nbkp.cli.volumes_cmd.check_mount_status")
    @patch("nbkp.cli.common.load_config")
    def test_json_output(self, mock_load: MagicMock, mock_check: MagicMock) -> None:
        config = _mount_config_for_status()
        mock_load.return_value = config
        mock_check.side_effect = [
            MountCapabilities(
                resolved_backend="systemd",
                device_present=True,
                luks_attached=True,
                mounted=False,
            ),
            MountCapabilities(
                resolved_backend="systemd",
                device_present=True,
                mounted=True,
            ),
        ]

        result = runner.invoke(
            app, ["disks", "status", "--config", "/f.yaml", "-o", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 3
        assert data[0]["volume"] == "encrypted-drive"
        assert data[0]["device_present"] is True
        assert data[0]["luks_attached"] is True
        assert data[0]["mounted"] is False
        assert data[1]["volume"] == "plain-drive"
        assert data[1]["mounted"] is True
        assert data[2]["volume"] == "no-mount"
        assert data[2]["strategy"] == "not managed"

    @patch("nbkp.cli.volumes_cmd.check_mount_status")
    @patch("nbkp.cli.common.load_config")
    def test_human_output(self, mock_load: MagicMock, mock_check: MagicMock) -> None:
        config = _mount_config_for_status()
        mock_load.return_value = config
        mock_check.return_value = MountCapabilities(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=True,
        )

        result = runner.invoke(app, ["disks", "status", "--config", "/f.yaml"])
        assert result.exit_code == 0
        assert "Volume Mount Status" in result.output

    @patch("nbkp.cli.volumes_cmd.check_mount_status")
    @patch("nbkp.cli.common.load_config")
    def test_name_filter(self, mock_load: MagicMock, mock_check: MagicMock) -> None:
        config = _mount_config_for_status()
        mock_load.return_value = config
        mock_check.return_value = MountCapabilities(
            resolved_backend="systemd",
            device_present=True,
            mounted=True,
        )

        result = runner.invoke(
            app,
            [
                "disks",
                "status",
                "--config",
                "/f.yaml",
                "-o",
                "json",
                "--name",
                "encrypted-drive",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["volume"] == "encrypted-drive"

    @patch("nbkp.cli.common.load_config")
    def test_no_mount_config(self, mock_load: MagicMock) -> None:
        config = Config(
            ssh_endpoints={},
            volumes={
                "plain": LocalVolume(slug="plain", path="/home/user"),
            },
            sync_endpoints={},
            syncs={},
        )
        mock_load.return_value = config

        result = runner.invoke(
            app, ["disks", "status", "--config", "/f.yaml", "-o", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["volume"] == "plain"
        assert data[0]["strategy"] == "not managed"
