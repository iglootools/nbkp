"""Tests for nbkp.sync.modes.btrfs: snapshot after rsync into staging, pruning."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.sync.results import SyncFailureKind, SyncOutcome
from tests.sync.runner_helpers import (
    FIXED_NOW,
    active_statuses,
    make_btrfs_config,
    make_btrfs_config_with_max,
    make_remote_same_server_btrfs_config,
    run_syncs,
)


class TestBtrfsSync:
    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_btrfs_snapshot_after_sync(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = make_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = run_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path == "/dst/snapshots/20240115T120000Z"
        mock_snap.assert_called_once()
        assert mock_snap.call_args.kwargs["now"] == FIXED_NOW
        assert mock_snap.call_args.kwargs["platform"] == "linux"
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_btrfs_snapshot_skipped_on_dry_run(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = make_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = run_syncs(config, sync_statuses, dry_run=True)
        assert results[0].success is True
        assert results[0].snapshot_path is None

    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_btrfs_no_link_dest(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = make_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        run_syncs(config, sync_statuses)

        # Btrfs workflow no longer passes --link-dest
        call_kwargs = mock_rsync.call_args
        assert call_kwargs.kwargs.get("link_dest") is None

    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_remote_same_server_with_btrfs(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = make_remote_same_server_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/backup/snapshots/20240115T120000Z"

        results = run_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path is not None
        mock_snap.assert_called_once()
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_snapshot_failure(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
    ) -> None:
        config = make_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.side_effect = SnapshotOperationError(
            SnapshotOp.CREATE, "/dst/snapshots/x", "btrfs failed"
        )

        results = run_syncs(config, sync_statuses)
        assert results[0].outcome == SyncOutcome.FAILED
        assert results[0].failure == SyncFailureKind.SNAPSHOT

    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.btrfs_prune_snapshots")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_auto_prune_after_snapshot(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = make_btrfs_config_with_max()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"
        mock_prune.return_value = ["/dst/snapshots/old"]

        results = run_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths == ("/dst/snapshots/old",)
        mock_prune.assert_called_once()

    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.btrfs_prune_snapshots")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_no_auto_prune_without_max_snapshots(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = make_btrfs_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = run_syncs(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths is None
        mock_prune.assert_not_called()
