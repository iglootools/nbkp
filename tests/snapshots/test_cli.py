"""Tests for nbkp snapshots CLI commands."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.preflight import (
    SyncError,
    SyncStatus,
    VolumeStatus,
)
from tests.clihelpers import (
    dst_ep_status,
    localhost_ssh_status,
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    src_ep_status,
    vol_status,
)


# ── Snapshot-specific config helpers ─────────────────────────


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
    local_ssh = localhost_ssh_status()
    vol_statuses = {
        name: vol_status(name, config, local_ssh) for name in config.volumes
    }
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=src_ep_status(
                sync.source,
                vol_statuses[config.sync_endpoints[sync.source].volume],
            ),
            destination_endpoint_status=dst_ep_status(
                sync.destination,
                vol_statuses[config.sync_endpoints[sync.destination].volume],
            ),
            errors=[],
        )
        for name, sync in config.syncs.items()
    }
    return vol_statuses, sync_statuses


class TestPruneCommand:
    @patch("nbkp.snapshots.cli.cmd_handler.prune.list_snapshots")
    @patch("nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_successful_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        local_ssh = localhost_ssh_status()
        vol_s = {name: vol_status(name, config, local_ssh) for name in config.volumes}
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(app, ["snapshots", "prune", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "OK" in result.output
        mock_prune.assert_called_once()

    @patch("nbkp.snapshots.cli.cmd_handler.prune.list_snapshots")
    @patch("nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_dry_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        local_ssh = localhost_ssh_status()
        vol_s = {name: vol_status(name, config, local_ssh) for name in config.volumes}
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s1",
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(
            app, ["snapshots", "prune", "--config", "/fake.yaml", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "dry run" in result.output
        mock_prune.assert_called_once()
        call_kwargs = mock_prune.call_args
        assert call_kwargs.kwargs.get("dry_run") is True

    @patch("nbkp.snapshots.cli.cmd_handler.prune.list_snapshots")
    @patch("nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_json_output(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        local_ssh = localhost_ssh_status()
        vol_s = {name: vol_status(name, config, local_ssh) for name in config.volumes}
        _, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [
            "/dst/snapshots/s2",
            "/dst/snapshots/s3",
        ]

        result = runner.invoke(
            app,
            [
                "snapshots",
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

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_no_syncs_to_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = sample_config()  # no btrfs snapshots
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["snapshots", "prune", "--config", "/fake.yaml"])
        assert result.exit_code == 0


_SNAP_1 = Snapshot(
    name="2026-03-01T10:00:00.000Z",
    timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
)
_SNAP_2 = Snapshot(
    name="2026-03-06T14:30:00.000Z",
    timestamp=datetime(2026, 3, 6, 14, 30, 0, tzinfo=timezone.utc),
)


class TestShowCommand:
    @patch("nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink")
    @patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_successful_show(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        vol_s, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_list.return_value = [_SNAP_1, _SNAP_2]
        mock_latest.return_value = _SNAP_2

        result = runner.invoke(app, ["snapshots", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "OK" in result.output

    @patch("nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink")
    @patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_json_output(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        vol_s, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_list.return_value = [_SNAP_1, _SNAP_2]
        mock_latest.return_value = _SNAP_2

        result = runner.invoke(
            app,
            ["snapshots", "show", "--config", "/fake.yaml", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["sync_slug"] == "s1"
        assert data[0]["snapshot_mode"] == "btrfs"
        assert len(data[0]["snapshots"]) == 2
        assert data[0]["latest"]["name"] == "2026-03-06T14:30:00.000Z"
        assert data[0]["max_snapshots"] == 3

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_no_snapshots_configured(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = sample_config()  # no snapshots
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["snapshots", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "SKIPPED" in result.output

    @patch("nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink")
    @patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots")
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_sync_filter(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        vol_s, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)
        mock_list.return_value = [_SNAP_1]
        mock_latest.return_value = _SNAP_1

        result = runner.invoke(
            app,
            ["snapshots", "show", "--config", "/fake.yaml", "--sync", "s1"],
        )
        assert result.exit_code == 0
        assert "OK" in result.output

    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_inactive_sync(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        local_ssh = localhost_ssh_status()
        vol_s = {name: vol_status(name, config, local_ssh) for name in config.volumes}
        sync_s = {
            name: SyncStatus(
                slug=name,
                config=sync,
                source_endpoint_status=src_ep_status(
                    sync.source,
                    vol_s[config.sync_endpoints[sync.source].volume],
                    sentinel_exists=False,
                ),
                destination_endpoint_status=dst_ep_status(
                    sync.destination,
                    vol_s[config.sync_endpoints[sync.destination].volume],
                ),
                errors=[SyncError.SOURCE_ENDPOINT_INACTIVE],
            )
            for name, sync in config.syncs.items()
        }
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["snapshots", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "SKIPPED" in result.output
        assert "inactive" in result.output

    @patch(
        "nbkp.snapshots.cli.cmd_handler.show.list_snapshots",
        side_effect=RuntimeError("connection failed"),
    )
    @patch("nbkp.preflight.cli.helpers.check_all_syncs")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_runtime_error(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        vol_s, sync_s = _prune_active_statuses(config)
        mock_checks.return_value = preflight(vol_s, sync_s)

        result = runner.invoke(app, ["snapshots", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 1
        assert "FAILED" in result.output
