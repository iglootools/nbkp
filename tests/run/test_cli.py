import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbkp.cli import app
from nbkp.config import SyncConfig, SyncEndpoint
from nbkp.config.testkit import write_config
from nbkp.preflight import SyncError
from nbkp.sync import ProgressMode, SyncFailureKind, SyncResult
from tests.cliwiring.test_mount import LOCK_FAILED, fake_disks_managed_mount

_DONE = subprocess.CompletedProcess(args=[], returncode=0, stdout="done", stderr="")


def _ok(slug: str, *, dry_run: bool = False) -> SyncResult:
    return SyncResult.succeeded(slug, dry_run, _DONE)


from tests.clidisplay import (
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
    def test_successful_run(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(app, ["run", "--config", str(config_path), "-o", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert [(r["sync_slug"], r["outcome"]) for r in data["results"]] == [
            ("photos-to-nas", "success")
        ]
        assert "hint" not in data

        result = runner.invoke(app, ["run", "--config", str(config_path)])
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("on_rsync_output") is None
        assert callable(call_kwargs.kwargs.get("on_sync_start"))
        assert callable(call_kwargs.kwargs.get("on_sync_end"))

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_displays_status_before_results(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(app, ["run", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        vol_pos = result.output.index("Volumes:")
        ok_pos = result.output.index("OK")
        assert vol_pos < ok_pos

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_failed_run(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
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

        result = runner.invoke(app, ["run", "--config", str(config_path), "-o", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["results"][0]["outcome"] == "failed"
        assert data["results"][0]["failure"] == "rsync"

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_dry_run(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=True)]

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--dry-run", "-o", "json"],
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["results"][0]["dry_run"] is True
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("dry_run") is True

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_json_output(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--output", "json"],
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
    def test_json_output_with_snapshot_timestamps(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Regression: statuses carrying a snapshot broke JSON serialization.

        `run` is the command where this matters most, since scripting it is
        the reason to ask for JSON at all — and any destination that has ever
        completed a sync has a populated ``latest``.
        """
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sync_statuses_with_snapshots(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--output", "json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["results"][0]["sync_slug"] == "photos-to-nas"
        assert data["syncs"][0]["destination_latest_snapshot"]["timestamp"].startswith(
            "2026-03-06T14:30:00"
        )

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_sync_filter(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--sync", "photos-to-nas"],
        )
        assert result.exit_code == 0
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]
        run_kwargs = mock_run.call_args
        assert run_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_progress(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--progress", "per-file"],
        )
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("progress") == ProgressMode.PER_FILE

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_exits_before_syncs_on_status_error(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_vol_statuses(config)
        sync_s = sample_error_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["run", "--config", str(config_path)])
        assert result.exit_code == 1
        mock_run.assert_not_called()

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_sentinel_only_proceeds_by_default(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_run.return_value = [_ok("photos-to-nas", dry_run=False)]

        result = runner.invoke(app, ["run", "--config", str(config_path)])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_sentinel_only_exits_when_strict(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--strictness", "ignore-none"],
        )
        assert result.exit_code == 1
        mock_run.assert_not_called()


class TestRunAbortHint:
    """The abort points at `preflight troubleshoot` with the user's flags."""

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_json_hint_carries_config_and_network_flags(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = sample_config()
        (tmp_path / "conf").mkdir()
        config_file = write_config(tmp_path / "conf", config, name="nbkp.yaml")
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_error_sync_statuses(config, vol_s)
        )
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
    def test_human_abort_suggests_troubleshoot(
        self,
        mock_checks: MagicMock,
        _mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_error_sync_statuses(config, vol_s)
        )

        result = runner.invoke(app, ["run", "-c", str(config_path)])

        assert result.exit_code == 1
        assert "nbkp preflight troubleshoot" in result.output

    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_abort_does_not_list_disabled_syncs(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Under ignore-none an inactive sync aborts the run, but a disabled
        one is never fatal and must not be reported as offending."""
        config = sample_config()
        off = SyncConfig(
            slug="off", source="ep-src", destination="ep-off", enabled=False
        )
        config = config.model_copy(
            update={
                "sync_endpoints": {
                    **config.sync_endpoints,
                    "ep-off": SyncEndpoint(slug="ep-off", volume="nas", subdir="off"),
                },
                "syncs": {**config.syncs, "off": off},
            }
        )
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        inactive = sync_s["photos-to-nas"]
        sync_s["off"] = inactive.model_copy(
            update={"slug": "off", "config": off, "errors": [SyncError.DISABLED]}
        )
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--strictness", "ignore-none"],
        )

        assert result.exit_code == 1
        assert "found errors in 1 sync:" in result.output
        assert "photos-to-nas:" in result.output
        assert "off:" not in result.output
        mock_run.assert_not_called()


class TestUnknownSyncFilter:
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_unknown_sync_is_an_error(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(
            app, ["run", "-c", str(config_path), "-o", "json", "--sync", "nope"]
        )

        assert result.exit_code == 2
        error = json.loads(result.output)["error"]
        assert (error["reason"], error["names"]) == ("unknown-sync", ["nope"])
        mock_checks.assert_not_called()

    def test_unknown_sync_is_reported_on_stderr(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["run", "-c", str(config_path), "--sync", "nope"])

        assert result.exit_code == 2
        assert "unknown sync name(s): nope" in result.stderr
        assert "photos-to-nas" in result.stderr


class TestUmountFailure:
    @patch(
        "nbkp.cliwiring.mount._disks_managed_mount",
        fake_disks_managed_mount(LOCK_FAILED),
    )
    @patch("nbkp.run.pipeline.run_all_syncs")
    @patch("nbkp.run.pipeline.check_all_syncs")
    def test_failed_lock_fails_the_run(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_all_active_sync_statuses(config, vol_s)
        )
        mock_run.return_value = [_ok("photos-to-nas")]

        result = runner.invoke(app, ["run", "-c", str(config_path), "-o", "json"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["results"][0]["outcome"] == "success"
        [umount] = data["umount"]
        assert umount["volume"] == "enc"
        assert umount["success"] is False
        assert umount["failure_reason"] == "lock_failed"
        assert umount["warning"] == "LUKS container still unlocked, manual lock needed"
