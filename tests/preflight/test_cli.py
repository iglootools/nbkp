"""Tests for nbkp preflight CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.preflight import SyncStatus
from tests.clihelpers import (
    config_with_locations,
    dst_ep_status,
    localhost_ssh_status,
    preflight,
    remote_ssh_status,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_sentinel_only_sync_statuses,
    sample_sync_statuses,
    sample_vol_statuses,
    src_ep_status,
    vol_status,
)


class TestLocationValidation:
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_unknown_location_rejected(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        mock_load.return_value = config_with_locations()
        result = runner.invoke(
            app, ["preflight", "check", "--config", "/f.yaml", "--location", "office"]
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "home" in result.output
        assert "travel" in result.output

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_unknown_exclude_location_rejected(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        mock_load.return_value = config_with_locations()
        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                "/f.yaml",
                "--exclude-location",
                "office",
            ],
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "--exclude-location" in result.output

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_known_location_accepted(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = config_with_locations()
        mock_load.return_value = config
        local_ssh = localhost_ssh_status()
        nas_ssh = remote_ssh_status("nas-home")
        vol_s = {
            "local-data": vol_status("local-data", config, local_ssh),
            "nas": vol_status("nas", config, nas_ssh),
        }
        src_ep = src_ep_status("ep-src", vol_s["local-data"])
        dst_ep = dst_ep_status("ep-dst", vol_s["nas"])
        sync_s = {
            slug: SyncStatus(
                slug=slug,
                config=config.syncs[slug],
                source_endpoint_status=src_ep,
                destination_endpoint_status=dst_ep,
                errors=[],
            )
            for slug in config.syncs
        }
        mock_checks.return_value = preflight(vol_s, sync_s)
        result = runner.invoke(
            app, ["preflight", "check", "--config", "/f.yaml", "--location", "home"]
        )
        assert result.exit_code == 0

    @patch("nbkp.clihelpers.config.load_config")
    def test_location_on_config_without_locations(self, mock_load: MagicMock) -> None:
        mock_load.return_value = sample_config()
        result = runner.invoke(
            app, ["preflight", "check", "--config", "/f.yaml", "--location", "home"]
        )
        assert result.exit_code == 2
        assert "no locations are defined" in result.output


class TestCheckCommand:
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_human_output_inactive(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        sync_s = sample_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["preflight", "check", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "Preflight" in result.output

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_human_output_all_active(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["preflight", "check", "--config", "/fake.yaml"])
        assert result.exit_code == 0

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_json_output_inactive(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        sync_s = sample_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["preflight", "check", "--config", "/fake.yaml", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_json_output_all_active(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["preflight", "check", "--config", "/fake.yaml", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_sentinel_only_exit_0_by_default(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["preflight", "check", "--config", "/fake.yaml"])
        assert result.exit_code == 0

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
    def test_sentinel_only_exit_1_when_strict(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                "/fake.yaml",
                "--strictness",
                "ignore-none",
            ],
        )
        assert result.exit_code == 1
