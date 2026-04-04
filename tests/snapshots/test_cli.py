"""Tests for nbkp snapshots CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
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
    @patch("nbkp.snapshots.pruner.list_snapshots")
    @patch("nbkp.snapshots.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
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

    @patch("nbkp.snapshots.pruner.list_snapshots")
    @patch("nbkp.snapshots.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
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

    @patch("nbkp.snapshots.pruner.list_snapshots")
    @patch("nbkp.snapshots.pruner.btrfs_prune_snapshots")
    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
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

    @patch("nbkp.cli.common.check_all_syncs")
    @patch("nbkp.clihelpers.config.load_config")
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
