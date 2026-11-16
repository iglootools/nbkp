import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.config.testkit import write_config
from nbkp.preflight import SyncStatus
from tests.clidisplay import (
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
    sample_sync_statuses_with_snapshots,
    sample_vol_statuses,
    src_ep_status,
    vol_status,
)


class TestLocationValidation:
    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_unknown_location_rejected(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, config_with_locations())
        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                str(config_path),
                "--location",
                "office",
            ],
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "home" in result.output
        assert "travel" in result.output

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_unknown_exclude_location_rejected(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, config_with_locations())
        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                str(config_path),
                "--exclude-location",
                "office",
            ],
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "--exclude-location" in result.output

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_unknown_location_json_error(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, config_with_locations())
        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                str(config_path),
                "--output",
                "json",
                "--location",
                "office",
            ],
        )
        assert result.exit_code == 2
        error = json.loads(result.stdout)["error"]
        assert error["reason"] == "unknown-location"
        assert "unknown location 'office'" in error["message"]
        assert "home" in error["message"]
        mock_checks.assert_not_called()

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_known_location_accepted(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = config_with_locations()
        config_path = write_config(tmp_path, config)
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
            app,
            ["preflight", "check", "--config", str(config_path), "--location", "home"],
        )
        assert result.exit_code == 0

    def test_location_on_config_without_locations(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())
        result = runner.invoke(
            app,
            ["preflight", "check", "--config", str(config_path), "--location", "home"],
        )
        assert result.exit_code == 2
        assert "no locations are defined" in result.output


class TestCheckCommand:
    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_human_output_inactive(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_vol_statuses(config)
        sync_s = sample_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app, ["preflight", "check", "--config", str(config_path)]
        )
        assert result.exit_code == 0
        assert "Preflight" in result.output

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_human_output_all_active(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app, ["preflight", "check", "--config", str(config_path)]
        )
        assert result.exit_code == 0

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_json_output_inactive(self, mock_checks: MagicMock, tmp_path: Path) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_vol_statuses(config)
        sync_s = sample_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["preflight", "check", "--config", str(config_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_json_output_all_active(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["preflight", "check", "--config", str(config_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_json_output_with_snapshot_timestamps(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        """A populated ``latest`` snapshot puts a datetime in the status tree.

        Regression: the statuses were dumped with a bare ``model_dump()``,
        which leaves ``Snapshot.timestamp`` as a ``datetime`` for
        ``json.dumps`` to reject.  The other JSON tests pass because their
        fixtures leave every snapshot field unset.
        """
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sync_statuses_with_snapshots(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            ["preflight", "check", "--config", str(config_path), "--output", "json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        sync = data["syncs"][0]
        assert sync["destination_latest_snapshot"]["timestamp"].startswith(
            "2026-03-06T14:30:00"
        )
        nested = sync["destination_endpoint_status"]["diagnostics"]["latest"]
        assert nested["snapshot"]["name"] == "2026-03-06T14:30:00.000Z"

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_sentinel_only_exit_0_by_default(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app, ["preflight", "check", "--config", str(config_path)]
        )
        assert result.exit_code == 0

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_sentinel_only_exit_1_when_strict(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "preflight",
                "check",
                "--config",
                str(config_path),
                "--strictness",
                "ignore-none",
            ],
        )
        assert result.exit_code == 1
