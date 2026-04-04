"""Tests for nbkp run CLI command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.sync import ProgressMode, SyncResult
from tests.clihelpers import (
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_error_sync_statuses,
    sample_sentinel_only_sync_statuses,
    sample_vol_statuses,
)


class TestRunCommand:
    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_successful_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="done",
            )
        ]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "photos" in result.output
        assert "OK" in result.output
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("on_rsync_output") is None
        assert callable(call_kwargs.kwargs.get("on_sync_start"))
        assert callable(call_kwargs.kwargs.get("on_sync_end"))

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_displays_status_before_results(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="done",
            )
        ]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        vol_pos = result.output.index("Volumes:")
        ok_pos = result.output.index("OK")
        assert vol_pos < ok_pos

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_failed_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=False,
                dry_run=False,
                rsync_exit_code=23,
                output="",
                detail="rsync failed",
            )
        ]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 1
        assert "FAILED" in result.output

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_dry_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=True,
                rsync_exit_code=0,
                output="",
            )
        ]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry run" in result.output
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("dry_run") is True

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_json_output(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="done",
            )
        ]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data
        assert "results" in data
        assert data["results"][0]["sync_slug"] == "photos-to-nas"
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("on_rsync_output") is None

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_sync_filter(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="",
            )
        ]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--sync", "photos-to-nas"],
        )
        assert result.exit_code == 0
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]
        run_kwargs = mock_run.call_args
        assert run_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_progress(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="",
            )
        ]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--progress", "per-file"],
        )
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("progress") == ProgressMode.PER_FILE

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_exits_before_syncs_on_status_error(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        sync_s = sample_error_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 1
        mock_run.assert_not_called()

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_sentinel_only_proceeds_by_default(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [
            SyncResult(
                sync_slug="photos-to-nas",
                success=True,
                dry_run=False,
                rsync_exit_code=0,
                output="done",
            )
        ]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_sentinel_only_exits_when_strict(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--strictness", "ignore-none"],
        )
        assert result.exit_code == 1
        mock_run.assert_not_called()
