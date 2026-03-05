"""Tests for nbkp.runner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    DestinationSyncEndpoint,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.check import (
    SyncReason,
    SyncStatus,
    VolumeReason,
    VolumeStatus,
)
from nbkp.sync import run_all_syncs


def _make_local_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="dst"),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"s1": sync},
    )


def _make_btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"s1": sync},
    )


def _make_btrfs_config_with_max() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
        ),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"s1": sync},
    )


def _make_remote_same_server_btrfs_config() -> Config:
    server = SshEndpoint(slug="server", host="nas.local", user="backup")
    src = RemoteVolume(
        slug="src",
        ssh_endpoint="server",
        path="/data",
    )
    dst = RemoteVolume(
        slug="dst",
        ssh_endpoint="server",
        path="/backup",
    )
    sync = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    )
    return Config(
        ssh_endpoints={"server": server},
        volumes={"src": src, "dst": dst},
        syncs={"s1": sync},
    )


def _active_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    vol_statuses = {
        name: VolumeStatus(
            slug=name,
            config=vol,
            reasons=[],
        )
        for name, vol in config.volumes.items()
    }
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_status=vol_statuses[sync.source.volume],
            destination_status=vol_statuses[sync.destination.volume],
            reasons=[],
        )
        for name, sync in config.syncs.items()
    }
    return vol_statuses, sync_statuses


def _inactive_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    vol_statuses = {
        name: VolumeStatus(
            slug=name,
            config=vol,
            reasons=[VolumeReason.UNREACHABLE],
        )
        for name, vol in config.volumes.items()
    }
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_status=vol_statuses[sync.source.volume],
            destination_status=vol_statuses[sync.destination.volume],
            reasons=[SyncReason.SOURCE_UNAVAILABLE],
        )
        for name, sync in config.syncs.items()
    }
    return vol_statuses, sync_statuses


class TestRunAllSyncs:
    @patch("nbkp.sync.runner.run_rsync")
    def test_successful_sync(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )

        results = run_all_syncs(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].rsync_exit_code == 0

    def test_inactive_sync(self) -> None:
        config = _make_local_config()
        _, sync_statuses = _inactive_statuses(config)

        results = run_all_syncs(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is False
        assert "not active" in (results[0].error or "")

    @patch("nbkp.sync.runner.run_rsync")
    def test_rsync_failure(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=23, stdout="", stderr="error"
        )

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is False
        assert results[0].rsync_exit_code == 23

    @patch("nbkp.sync.runner.run_rsync")
    def test_filter_by_sync_slug(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )

        results = run_all_syncs(
            config, sync_statuses, only_syncs=["nonexistent"]
        )
        assert len(results) == 0

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_snapshot_after_sync(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path == "/dst/snapshots/20240115T120000Z"
        mock_snap.assert_called_once()
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_snapshot_skipped_on_dry_run(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )

        results = run_all_syncs(config, sync_statuses, dry_run=True)
        assert results[0].success is True
        assert results[0].snapshot_path is None

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_no_link_dest(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        run_all_syncs(config, sync_statuses)

        # Btrfs workflow no longer passes --link-dest
        call_kwargs = mock_rsync.call_args
        assert call_kwargs.kwargs.get("link_dest") is None

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_remote_same_server_with_btrfs(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = _make_remote_same_server_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.return_value = "/backup/snapshots/20240115T120000Z"

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path is not None
        mock_snap.assert_called_once()
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_snapshot_failure(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.side_effect = RuntimeError("btrfs failed")

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is False
        assert "Snapshot failed" in (results[0].error or "")

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.btrfs_prune_snapshots")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_auto_prune_after_snapshot(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config_with_max()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"
        mock_prune.return_value = ["/dst/snapshots/old"]

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths == ["/dst/snapshots/old"]
        mock_prune.assert_called_once()

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.btrfs_prune_snapshots")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_no_auto_prune_without_max_snapshots(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=0, stdout="done\n", stderr=""
        )
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = run_all_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths is None
        mock_prune.assert_not_called()
