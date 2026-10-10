"""Tests for nbkp run CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbkp.cli import app
from nbkp.sync import ProgressMode, SyncFailureKind, SyncResult

_DONE = subprocess.CompletedProcess(args=[], returncode=0, stdout="done", stderr="")


def _ok(slug: str, *, dry_run: bool = False) -> SyncResult:
    return SyncResult.succeeded(slug, dry_run, _DONE)


from tests.clihelpers import (
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_error_sync_statuses,
    sample_sentinel_only_sync_statuses,
    sample_sync_statuses_with_snapshots,
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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml", "-o", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert [(r["sync_slug"], r["outcome"]) for r in data["results"]] == [
            ("photos-to-nas", "success")
        ]
        assert "hint" not in data

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 0
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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

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
            SyncResult.failed(
                "photos-to-nas",
                False,
                SyncFailureKind.RSYNC,
                "rsync exited with code 23",
                rsync_exit_code=23,
            )
        ]

        result = runner.invoke(app, ["run", "--config", "/fake.yaml", "-o", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["results"][0]["outcome"] == "failed"
        assert data["results"][0]["failure"] == "rsync"

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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=True)]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--dry-run", "-o", "json"],
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["results"][0]["dry_run"] is True
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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

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
    def test_json_output_with_snapshot_timestamps(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Regression: statuses carrying a snapshot broke JSON serialization.

        `run` is the command where this matters most, since scripting it is
        the reason to ask for JSON at all — and any destination that has ever
        completed a sync has a populated ``latest``.
        """
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sync_statuses_with_snapshots(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(
            app,
            ["run", "--config", "/fake.yaml", "--output", "json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["results"][0]["sync_slug"] == "photos-to-nas"
        assert data["syncs"][0]["destination_latest_snapshot"]["timestamp"].startswith(
            "2026-03-06T14:30:00"
        )

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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

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
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

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


class TestRunAbortHint:
    """The abort points at `preflight troubleshoot` with the user's flags."""

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_json_hint_carries_config_and_network_flags(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_error_sync_statuses(config, vol_s)
        )
        config_file = tmp_path / "conf" / "nbkp.yaml"
        config_file.parent.mkdir()
        config_file.write_text("")  # existence is checked before load_config
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            [
                "run",
                "-c",
                str(config_file),
                "-N",
                "private",
                "-o",
                "json",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["results"] == []
        # Location flags are covered by TestTroubleshootCommand: the sample
        # config defines no locations, so the CLI rejects them here.
        assert (
            data["hint"] == "nbkp preflight troubleshoot -c conf/nbkp.yaml -N private"
        )
        mock_run.assert_not_called()

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_human_abort_suggests_troubleshoot(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        _mock_run: MagicMock,
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_error_sync_statuses(config, vol_s)
        )

        result = runner.invoke(app, ["run"])

        assert result.exit_code == 1
        assert "nbkp preflight troubleshoot" in result.output
