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
from nbkp.sync.runner import SyncOutcome


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
        assert "not active" in (results[0].detail or "")

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
        assert "Snapshot failed" in (results[0].detail or "")

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


def _make_chain_config() -> Config:
    """A→B chain: s1 writes to 'mid', s2 reads from 'mid'."""
    src = LocalVolume(slug="src", path="/src")
    mid = LocalVolume(slug="mid", path="/mid")
    dst = LocalVolume(slug="dst", path="/dst")
    s1 = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="mid"),
    )
    s2 = SyncConfig(
        slug="s2",
        source=SyncEndpoint(volume="mid"),
        destination=DestinationSyncEndpoint(volume="dst"),
    )
    return Config(
        volumes={"src": src, "mid": mid, "dst": dst},
        syncs={"s1": s1, "s2": s2},
    )


def _make_independent_config() -> Config:
    """Two independent syncs with no shared volumes."""
    src1 = LocalVolume(slug="src1", path="/src1")
    dst1 = LocalVolume(slug="dst1", path="/dst1")
    src2 = LocalVolume(slug="src2", path="/src2")
    dst2 = LocalVolume(slug="dst2", path="/dst2")
    s1 = SyncConfig(
        slug="s1",
        source=SyncEndpoint(volume="src1"),
        destination=DestinationSyncEndpoint(volume="dst1"),
    )
    s2 = SyncConfig(
        slug="s2",
        source=SyncEndpoint(volume="src2"),
        destination=DestinationSyncEndpoint(volume="dst2"),
    )
    return Config(
        volumes={
            "src1": src1,
            "dst1": dst1,
            "src2": src2,
            "dst2": dst2,
        },
        syncs={"s1": s1, "s2": s2},
    )


class TestFailurePropagation:
    @patch("nbkp.sync.runner.run_rsync")
    def test_dependent_sync_cancelled_on_failure(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_chain_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=23, stdout="", stderr="error"
        )

        results = run_all_syncs(config, sync_statuses)
        assert len(results) == 2

        # s1 failed
        r1 = next(r for r in results if r.sync_slug == "s1")
        assert r1.success is False
        assert r1.outcome == SyncOutcome.FAILED
        assert r1.rsync_exit_code == 23

        # s2 cancelled
        r2 = next(r for r in results if r.sync_slug == "s2")
        assert r2.success is False
        assert r2.outcome == SyncOutcome.CANCELLED
        assert "'s1'" in (r2.detail or "")

    @patch("nbkp.sync.runner.run_rsync")
    def test_independent_sync_not_cancelled(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_independent_config()
        _, sync_statuses = _active_statuses(config)

        call_count = 0

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(returncode=23, stdout="", stderr="error")
            return MagicMock(returncode=0, stdout="done\n", stderr="")

        mock_rsync.side_effect = _side_effect

        results = run_all_syncs(config, sync_statuses)
        assert len(results) == 2
        # One failed, one succeeded (order may vary)
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        # The failure is a real rsync failure, not a cancellation
        assert failures[0].rsync_exit_code == 23
        assert failures[0].outcome == SyncOutcome.FAILED

    @patch("nbkp.sync.runner.run_rsync")
    def test_transitive_cancellation(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        """A→B→C chain: A fails → both B and C cancelled."""
        v0 = LocalVolume(slug="v0", path="/v0")
        v1 = LocalVolume(slug="v1", path="/v1")
        v2 = LocalVolume(slug="v2", path="/v2")
        v3 = LocalVolume(slug="v3", path="/v3")
        config = Config(
            volumes={
                "v0": v0,
                "v1": v1,
                "v2": v2,
                "v3": v3,
            },
            syncs={
                "a": SyncConfig(
                    slug="a",
                    source=SyncEndpoint(volume="v0"),
                    destination=DestinationSyncEndpoint(volume="v1"),
                ),
                "b": SyncConfig(
                    slug="b",
                    source=SyncEndpoint(volume="v1"),
                    destination=DestinationSyncEndpoint(volume="v2"),
                ),
                "c": SyncConfig(
                    slug="c",
                    source=SyncEndpoint(volume="v2"),
                    destination=DestinationSyncEndpoint(volume="v3"),
                ),
            },
        )
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(
            returncode=23, stdout="", stderr="error"
        )

        results = run_all_syncs(config, sync_statuses)
        assert len(results) == 3

        ra = next(r for r in results if r.sync_slug == "a")
        rb = next(r for r in results if r.sync_slug == "b")
        rc = next(r for r in results if r.sync_slug == "c")

        assert ra.success is False
        assert ra.outcome == SyncOutcome.FAILED
        assert ra.rsync_exit_code == 23

        assert rb.success is False
        assert rb.outcome == SyncOutcome.CANCELLED

        assert rc.success is False
        assert rc.outcome == SyncOutcome.CANCELLED
