"""Tests for nbkp.cli package."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
    SyncError,
    SyncStatus,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.sync import ProgressMode, SyncResult

runner = CliRunner()


def _strip_panel(text: str) -> str:
    """Strip Rich panel border characters and normalize whitespace."""
    text = re.sub(r"[╭╮╰╯│─]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _sample_config() -> Config:
    src = LocalVolume(slug="local-data", path="/mnt/data")
    nas_server = SshEndpoint(
        slug="nas-server",
        host="nas.example.com",
        port=5022,
        user="backup",
    )
    dst = RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-server",
        path="/volume1/backups",
    )
    ep_src = SyncEndpoint(slug="ep-src", volume="local-data", subdir="photos")
    ep_dst = SyncEndpoint(slug="ep-dst", volume="nas", subdir="photos-backup")
    sync = SyncConfig(
        slug="photos-to-nas",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        ssh_endpoints={"nas-server": nas_server},
        volumes={"local-data": src, "nas": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"photos-to-nas": sync},
    )


def _sample_vol_statuses(
    config: Config,
) -> dict[str, VolumeStatus]:
    return {
        "local-data": VolumeStatus(
            slug="local-data",
            config=config.volumes["local-data"],
            diagnostics=VolumeDiagnostics(),
            errors=[],
        ),
        "nas": VolumeStatus(
            slug="nas",
            config=config.volumes["nas"],
            diagnostics=VolumeDiagnostics(ssh_reachable=False),
            errors=[VolumeError.UNREACHABLE],
        ),
    }


def _sample_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_status=vol_statuses["local-data"],
            destination_status=vol_statuses["nas"],
            errors=[SyncError.DST_VOL_UNAVAILABLE],
        ),
    }


def _sample_error_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_status=vol_statuses["local-data"],
            destination_status=vol_statuses["nas"],
            errors=[SyncError.DST_VOL_RSYNC_NOT_FOUND],
        ),
    }


def _sample_sentinel_only_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_status=vol_statuses["local-data"],
            destination_status=vol_statuses["nas"],
            errors=[
                SyncError.SRC_EP_SENTINEL_NOT_FOUND,
                SyncError.DST_EP_SENTINEL_NOT_FOUND,
            ],
        ),
    }


def _sample_all_active_vol_statuses(
    config: Config,
) -> dict[str, VolumeStatus]:
    return {
        "local-data": VolumeStatus(
            slug="local-data",
            config=config.volumes["local-data"],
            diagnostics=VolumeDiagnostics(),
            errors=[],
        ),
        "nas": VolumeStatus(
            slug="nas",
            config=config.volumes["nas"],
            diagnostics=VolumeDiagnostics(),
            errors=[],
        ),
    }


def _sample_all_active_sync_statuses(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SyncStatus]:
    return {
        "photos-to-nas": SyncStatus(
            slug="photos-to-nas",
            config=config.syncs["photos-to-nas"],
            source_status=vol_statuses["local-data"],
            destination_status=vol_statuses["nas"],
            errors=[],
        ),
    }


class TestConfigShowCommand:
    @patch("nbkp.cli.common.load_config")
    def test_human_output(self, mock_load: MagicMock) -> None:
        config = _sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["config", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        assert "local-data" in result.output
        assert "nas" in result.output
        assert "photos" in result.output

    @patch("nbkp.cli.common.load_config")
    def test_human_output_shows_servers(self, mock_load: MagicMock) -> None:
        config = _sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["config", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "SSH Endpoints:" in result.output
        assert "nas-server" in result.output
        assert "nas.example.com" in result.output

    @patch("nbkp.cli.common.load_config")
    def test_json_output(self, mock_load: MagicMock) -> None:
        config = _sample_config()
        mock_load.return_value = config

        result = runner.invoke(
            app,
            [
                "config",
                "show",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data
        assert "ssh-endpoints" in data

    @patch(
        "nbkp.cli.common.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(
            app,
            ["config", "show", "--config", "/bad.yaml"],
        )
        assert result.exit_code == 2


def _config_with_locations() -> Config:
    """Config with location-tagged SSH endpoints for filter validation tests."""
    server_home = SshEndpoint(slug="nas-home", host="192.168.1.50", location="home")
    server_travel = SshEndpoint(
        slug="nas-travel", host="nas.example.com", location="travel"
    )
    src = LocalVolume(slug="local-data", path="/mnt/data")
    dst = RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-home",
        ssh_endpoints=["nas-home", "nas-travel"],
        path="/volume1/backups",
    )
    ep_src = SyncEndpoint(slug="ep-src", volume="local-data")
    ep_dst = SyncEndpoint(slug="ep-dst", volume="nas")
    sync = SyncConfig(slug="backup", source="ep-src", destination="ep-dst")
    return Config(
        ssh_endpoints={"nas-home": server_home, "nas-travel": server_travel},
        volumes={"local-data": src, "nas": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"backup": sync},
    )


class TestLocationValidation:
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_unknown_location_rejected(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        mock_load.return_value = _config_with_locations()
        result = runner.invoke(
            app, ["check", "--config", "/f.yaml", "--location", "office"]
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "home" in result.output
        assert "travel" in result.output

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_unknown_exclude_location_rejected(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        mock_load.return_value = _config_with_locations()
        result = runner.invoke(
            app, ["check", "--config", "/f.yaml", "--exclude-location", "office"]
        )
        assert result.exit_code == 2
        assert "unknown location 'office'" in result.output
        assert "--exclude-location" in result.output

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_known_location_accepted(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _config_with_locations()
        mock_load.return_value = config
        vol_s = {
            slug: VolumeStatus(
                slug=slug,
                config=config.volumes[slug],
                diagnostics=VolumeDiagnostics(),
                errors=[],
            )
            for slug in config.volumes
        }
        sync_s = {
            slug: SyncStatus(
                slug=slug,
                config=config.syncs[slug],
                source_status=vol_s["local-data"],
                destination_status=vol_s["nas"],
                errors=[],
            )
            for slug in config.syncs
        }
        mock_checks.return_value = (vol_s, sync_s)
        result = runner.invoke(
            app, ["check", "--config", "/f.yaml", "--location", "home"]
        )
        assert result.exit_code == 0

    @patch("nbkp.cli.common.load_config")
    def test_location_on_config_without_locations(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _sample_config()
        result = runner.invoke(
            app, ["check", "--config", "/f.yaml", "--location", "home"]
        )
        assert result.exit_code == 2
        assert "no locations are defined" in result.output


class TestCheckCommand:
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_human_output_inactive(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_vol_statuses(config)
        sync_s = _sample_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(app, ["check", "--config", "/fake.yaml"])
        # Unreachable is not an error in non-strict mode
        assert result.exit_code == 0
        # Rich truncates heavily in narrow test terminals, so only
        # check that the panel rendered (contains the title).
        assert "Preflight" in result.output

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_human_output_all_active(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(app, ["check", "--config", "/fake.yaml"])
        assert result.exit_code == 0

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_json_output_inactive(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_vol_statuses(config)
        sync_s = _sample_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "check",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        # Unreachable is not an error in non-strict mode
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_json_output_all_active(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "check",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_sentinel_only_exit_0_by_default(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(app, ["check", "--config", "/fake.yaml"])
        assert result.exit_code == 0

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_sentinel_only_exit_1_when_strict(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "check",
                "--config",
                "/fake.yaml",
                "--strict",
            ],
        )
        assert result.exit_code == 1


class TestRunCommand:
    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_successful_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_displays_status_before_results(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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
        # Status section appears before results section
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        vol_pos = result.output.index("Volumes:")
        ok_pos = result.output.index("OK")
        assert vol_pos < ok_pos

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_failed_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_dry_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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
        # Verify dry_run=True is propagated to check_all_syncs
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("dry_run") is True

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_json_output(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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
            [
                "run",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data
        assert "results" in data
        assert data["results"][0]["sync_slug"] == "photos-to-nas"
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("on_rsync_output") is None

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_sync_filter(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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
            [
                "run",
                "--config",
                "/fake.yaml",
                "--sync",
                "photos-to-nas",
            ],
        )
        assert result.exit_code == 0
        check_kwargs = mock_checks.call_args
        assert check_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]
        run_kwargs = mock_run.call_args
        assert run_kwargs.kwargs.get("only_syncs") == ["photos-to-nas"]

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_progress(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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
            [
                "run",
                "--config",
                "/fake.yaml",
                "--progress",
                "per-file",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("progress") == ProgressMode.PER_FILE

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_exits_before_syncs_on_status_error(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_vol_statuses(config)
        sync_s = _sample_error_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(app, ["run", "--config", "/fake.yaml"])
        assert result.exit_code == 1
        mock_run.assert_not_called()

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_sentinel_only_proceeds_by_default(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)
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

    @patch("nbkp.sync.pipeline.run_all_syncs")
    @patch("nbkp.sync.pipeline.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_sentinel_only_exits_when_strict(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        config = _sample_config()
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_sentinel_only_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(
            app,
            [
                "run",
                "--config",
                "/fake.yaml",
                "--strict",
            ],
        )
        assert result.exit_code == 1
        mock_run.assert_not_called()


def _prune_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    ep_src = SyncEndpoint(slug="ep-src", volume="src")
    ep_dst = SyncEndpoint(
        slug="ep-dst",
        volume="dst",
        btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=3),
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"s1": sync},
    )


def _prune_active_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    vol_statuses = {
        name: VolumeStatus(
            slug=name,
            config=vol,
            diagnostics=VolumeDiagnostics(),
            errors=[],
        )
        for name, vol in config.volumes.items()
    }
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_status=vol_statuses[config.sync_endpoints[sync.source].volume],
            destination_status=vol_statuses[
                config.sync_endpoints[sync.destination].volume
            ],
            errors=[],
        )
        for name, sync in config.syncs.items()
    }
    return vol_statuses, sync_statuses


class TestPruneCommand:
    @patch("nbkp.sync.pruner.list_snapshots")
    @patch("nbkp.sync.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_successful_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = (
            {
                name: VolumeStatus(
                    slug=name,
                    config=config.volumes[name],
                    diagnostics=VolumeDiagnostics(),
                    errors=[],
                )
                for name in config.volumes
            },
            sync_s,
        )
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(app, ["prune", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "OK" in result.output
        mock_prune.assert_called_once()

    @patch("nbkp.sync.pruner.list_snapshots")
    @patch("nbkp.sync.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_dry_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = (
            {
                name: VolumeStatus(
                    slug=name,
                    config=config.volumes[name],
                    diagnostics=VolumeDiagnostics(),
                    errors=[],
                )
                for name in config.volumes
            },
            sync_s,
        )
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s1",
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(app, ["prune", "--config", "/fake.yaml", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.output
        mock_prune.assert_called_once()
        call_kwargs = mock_prune.call_args
        assert call_kwargs.kwargs.get("dry_run") is True

    @patch("nbkp.sync.pruner.list_snapshots")
    @patch("nbkp.sync.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_json_output(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = (
            {
                name: VolumeStatus(
                    slug=name,
                    config=config.volumes[name],
                    diagnostics=VolumeDiagnostics(),
                    errors=[],
                )
                for name in config.volumes
            },
            sync_s,
        )
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(
            app,
            [
                "prune",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["sync_slug"] == "s1"
        assert len(data[0]["deleted"]) == 1

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.cli.common.load_config")
    def test_no_syncs_to_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _sample_config()  # no btrfs snapshots
        mock_load.return_value = config
        vol_s = _sample_all_active_vol_statuses(config)
        sync_s = _sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = (vol_s, sync_s)

        result = runner.invoke(app, ["prune", "--config", "/fake.yaml"])
        assert result.exit_code == 0


class TestConfigError:
    @patch(
        "nbkp.cli.common.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_check_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["check", "--config", "/bad.yaml"])
        assert result.exit_code == 2

    @patch(
        "nbkp.cli.common.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_run_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["run", "--config", "/bad.yaml"])
        assert result.exit_code == 2

    def test_plain_error_message(self) -> None:
        from nbkp.config import ConfigError

        from nbkp.config import ConfigErrorReason

        err = ConfigError(
            "Config file not found: /bad.yaml",
            reason=ConfigErrorReason.FILE_NOT_FOUND,
        )
        with patch("nbkp.cli.common.load_config", side_effect=err):
            result = runner.invoke(app, ["check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = _strip_panel(result.output)
        assert "Config file not found: /bad.yaml" in out

    def test_validation_error_message(self) -> None:
        from nbkp.config import ConfigError, ConfigErrorReason
        from pydantic import ValidationError
        from nbkp.config.protocol import Config

        try:
            Config.model_validate({"volumes": {"v": {"type": "ftp", "path": "/x"}}})
        except ValidationError as ve:
            err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
            err.__cause__ = ve

        with patch("nbkp.cli.common.load_config", side_effect=err):
            result = runner.invoke(app, ["check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = _strip_panel(result.output)
        assert "volumes → v" in out
        assert "does not match any of the expected tags" in out

    def test_yaml_error_message(self) -> None:
        import yaml
        from nbkp.config import ConfigError, ConfigErrorReason

        try:
            yaml.safe_load("not_a_list:\n  - [invalid")
        except yaml.YAMLError as ye:
            err = ConfigError(
                f"Invalid YAML in /bad.yaml: {ye}",
                reason=ConfigErrorReason.INVALID_YAML,
            )
            err.__cause__ = ye

        with patch("nbkp.cli.common.load_config", side_effect=err):
            result = runner.invoke(app, ["check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = _strip_panel(result.output)
        assert "Invalid YAML" in out

    def test_cross_reference_error_message(self) -> None:
        from nbkp.config import ConfigError, ConfigErrorReason
        from pydantic import ValidationError
        from nbkp.config.protocol import Config

        try:
            Config.model_validate(
                {
                    "ssh-endpoints": {},
                    "volumes": {
                        "v": {
                            "type": "remote",
                            "ssh-endpoint": "missing",
                            "path": "/x",
                        },
                    },
                    "syncs": {},
                }
            )
        except ValidationError as ve:
            err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
            err.__cause__ = ve

        with patch("nbkp.cli.common.load_config", side_effect=err):
            result = runner.invoke(app, ["check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = _strip_panel(result.output)
        assert "unknown ssh-endpoint 'missing'" in out


class TestShCommand:
    @patch("nbkp.cli.common.load_config")
    def test_generates_script(self, mock_load: MagicMock) -> None:
        config = _sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["sh", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "#!/bin/bash" in result.output
        assert "set -euo pipefail" in result.output
        assert "sync_photos_to_nas()" in result.output

    @patch("nbkp.cli.common.load_config")
    def test_config_path_in_header(self, mock_load: MagicMock) -> None:
        config = _sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["sh", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "# Config: /fake.yaml" in result.output

    @patch("nbkp.cli.common.load_config")
    def test_output_file(self, mock_load: MagicMock, tmp_path: object) -> None:
        import pathlib
        import stat

        tp = pathlib.Path(str(tmp_path))
        config = _sample_config()
        mock_load.return_value = config
        out = tp / "backup.sh"

        result = runner.invoke(
            app,
            ["sh", "--config", "/fake.yaml", "-o", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "#!/bin/bash" in content
        assert "sync_photos_to_nas()" in content
        mode = out.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP

    def test_relative_without_output_file(self) -> None:
        result = runner.invoke(
            app,
            ["sh", "--config", "/fake.yaml", "--relative-src"],
        )
        assert result.exit_code == 2

    @patch("nbkp.cli.common.load_config")
    def test_relative_with_output_file(
        self,
        mock_load: MagicMock,
        tmp_path: object,
    ) -> None:
        import pathlib

        tp = pathlib.Path(str(tmp_path))
        config = _sample_config()
        mock_load.return_value = config
        out = tp / "backup.sh"

        result = runner.invoke(
            app,
            [
                "sh",
                "--config",
                "/fake.yaml",
                "-o",
                str(out),
                "--relative-src",
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "NBKP_SCRIPT_DIR" in content

    @patch(
        "nbkp.cli.common.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["sh", "--config", "/bad.yaml"])
        assert result.exit_code == 2
