"""Tests for nbkp.btrfs."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from nbkp.sync.snapshots.btrfs import (
    create_snapshot,
    delete_snapshot,
    prune_snapshots,
)
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.snapshots.common import create_snapshot_timestamp


def _local_config() -> tuple[Config, SyncConfig]:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="backup",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


def _remote_config() -> tuple[Config, SyncConfig]:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst_server = SshEndpoint(
        slug="nas-server",
        host="nas.local",
        user="admin",
    )
    dst = RemoteVolume(
        slug="dst",
        ssh_endpoint="nas-server",
        path="/backup",
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        ssh_endpoints={"nas-server": dst_server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="data",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


class TestCreateSnapshotLocal:
    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, sync = _local_config()
        from datetime import datetime, timezone

        fixed_now = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=timezone.utc)
        dst_vol = config.volumes["dst"]
        expected_ts = create_snapshot_timestamp(fixed_now, dst_vol)
        path = create_snapshot(sync, config, now=fixed_now)
        assert path == f"/mnt/dst/backup/snapshots/{expected_ts.name}"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            "/mnt/dst/backup/staging",
            f"/mnt/dst/backup/snapshots/{expected_ts.name}",
        ]

    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
        config, sync = _local_config()
        from datetime import datetime, timezone

        fixed_now = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(RuntimeError, match="btrfs snapshot"):
            create_snapshot(sync, config, now=fixed_now)


class TestCreateSnapshotRemote:
    @patch("nbkp.sync.snapshots.btrfs.run_remote_command")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)
        from datetime import datetime, timezone

        fixed_now = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=timezone.utc)
        path = create_snapshot(sync, config, now=fixed_now, resolved_endpoints=resolved)
        assert path == "/backup/data/snapshots/2024-01-15T12:00:00.000Z"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == config.ssh_endpoints["nas-server"]
        assert call_args[0][1] == [
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            "/backup/data/staging",
            "/backup/data/snapshots/2024-01-15T12:00:00.000Z",
        ]


def _local_config_spaces() -> tuple[Config, SyncConfig]:
    src = LocalVolume(slug="src", path="/mnt/my src")
    dst = LocalVolume(slug="dst", path="/mnt/my dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="my backup",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


def _remote_config_spaces() -> tuple[Config, SyncConfig]:
    src = LocalVolume(slug="src", path="/mnt/my src")
    dst_server = SshEndpoint(
        slug="nas-server",
        host="nas.local",
        user="admin",
    )
    dst = RemoteVolume(
        slug="dst",
        ssh_endpoint="nas-server",
        path="/my backup",
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        ssh_endpoints={"nas-server": dst_server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="my data",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


class TestCreateSnapshotLocalSpaces:
    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, sync = _local_config_spaces()
        from datetime import datetime, timezone

        fixed_now = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=timezone.utc)
        dst_vol = config.volumes["dst"]
        expected_ts = create_snapshot_timestamp(fixed_now, dst_vol)
        path = create_snapshot(sync, config, now=fixed_now)
        assert path == f"/mnt/my dst/my backup/snapshots/{expected_ts.name}"
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            "/mnt/my dst/my backup/staging",
            f"/mnt/my dst/my backup/snapshots/{expected_ts.name}",
        ]


class TestCreateSnapshotRemoteSpaces:
    @patch("nbkp.sync.snapshots.btrfs.run_remote_command")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, sync = _remote_config_spaces()
        resolved = resolve_all_endpoints(config)
        from datetime import datetime, timezone

        fixed_now = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=timezone.utc)
        path = create_snapshot(sync, config, now=fixed_now, resolved_endpoints=resolved)
        assert path == "/my backup/my data/snapshots/2024-01-15T12:00:00.000Z"
        call_args = mock_run.call_args
        assert call_args[0][1] == [
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            "/my backup/my data/staging",
            "/my backup/my data/snapshots/2024-01-15T12:00:00.000Z",
        ]


class TestDeleteSnapshotLocal:
    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, _ = _local_config()
        dst_vol = config.volumes["dst"]
        path = "/mnt/dst/backup/snapshots/20240101T000000Z"

        delete_snapshot(path, dst_vol, {})
        assert mock_run.call_count == 2
        mock_run.assert_has_calls(
            [
                call(
                    ["btrfs", "property", "set", path, "ro", "false"],
                    capture_output=True,
                    text=True,
                ),
                call(
                    ["btrfs", "subvolume", "delete", path],
                    capture_output=True,
                    text=True,
                ),
            ]
        )

    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_failure_on_property_set(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
        config, _ = _local_config()
        dst_vol = config.volumes["dst"]

        with pytest.raises(RuntimeError, match="btrfs property set ro=false"):
            delete_snapshot(
                "/mnt/dst/backup/snapshots/20240101T000000Z",
                dst_vol,
                {},
            )

    @patch("nbkp.sync.snapshots.btrfs.subprocess.run")
    def test_failure_on_delete(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=1, stderr="permission denied"),
        ]
        config, _ = _local_config()
        dst_vol = config.volumes["dst"]

        with pytest.raises(RuntimeError, match="btrfs delete"):
            delete_snapshot(
                "/mnt/dst/backup/snapshots/20240101T000000Z",
                dst_vol,
                {},
            )


class TestDeleteSnapshotRemote:
    @patch("nbkp.sync.snapshots.btrfs.run_remote_command")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config, _ = _remote_config()
        resolved = resolve_all_endpoints(config)
        dst_vol = config.volumes["dst"]
        path = "/backup/data/snapshots/20240101T000000Z"
        server = config.ssh_endpoints["nas-server"]

        delete_snapshot(path, dst_vol, resolved)
        assert mock_run.call_count == 2
        mock_run.assert_has_calls(
            [
                call(
                    server,
                    ["btrfs", "property", "set", path, "ro", "false"],
                    [],
                ),
                call(
                    server,
                    ["btrfs", "subvolume", "delete", path],
                    [],
                ),
            ]
        )


class TestPruneSnapshotsLocal:
    @patch(
        "nbkp.sync.snapshots.common.read_latest_symlink",
        return_value=None,
    )
    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_prunes_oldest(self, mock_run: MagicMock, mock_latest: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240102T000000Z\n20240103T000000Z\n",
            stderr="",
        )
        config, sync = _local_config()

        deleted = prune_snapshots(sync, config, max_snapshots=1)
        assert deleted == [
            "/mnt/dst/backup/snapshots/20240101T000000Z",
            "/mnt/dst/backup/snapshots/20240102T000000Z",
        ]
        # ls call + 2 × (property set + delete) calls
        assert mock_run.call_count == 5

    @patch(
        "nbkp.sync.snapshots.common.read_latest_symlink",
        return_value=None,
    )
    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_nothing_to_prune(
        self, mock_run: MagicMock, mock_latest: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240102T000000Z\n",
            stderr="",
        )
        config, sync = _local_config()

        deleted = prune_snapshots(sync, config, max_snapshots=5)
        assert deleted == []
        # Only the ls call
        assert mock_run.call_count == 1

    @patch(
        "nbkp.sync.snapshots.common.read_latest_symlink",
        return_value=None,
    )
    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_dry_run(self, mock_run: MagicMock, mock_latest: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240102T000000Z\n20240103T000000Z\n",
            stderr="",
        )
        config, sync = _local_config()

        deleted = prune_snapshots(sync, config, max_snapshots=1, dry_run=True)
        assert deleted == [
            "/mnt/dst/backup/snapshots/20240101T000000Z",
            "/mnt/dst/backup/snapshots/20240102T000000Z",
        ]
        # Only the ls call, no delete calls
        assert mock_run.call_count == 1

    @patch("nbkp.sync.snapshots.common.read_latest_symlink")
    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_protects_latest_snapshot(
        self, mock_run: MagicMock, mock_latest: MagicMock
    ) -> None:
        """The snapshot that latest points to must not be pruned."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("20240101T000000Z\n20240102T000000Z\n20240103T000000Z\n"),
            stderr="",
        )
        # latest points to the second-oldest snapshot
        from nbkp.fsprotocol import Snapshot

        mock_latest.return_value = Snapshot.from_name("20240102T000000Z")
        config, sync = _local_config()

        deleted = prune_snapshots(sync, config, max_snapshots=1)
        # 20240102T000000Z is skipped; 20240101T000000Z and then
        # 20240103T000000Z are candidates, but we only need to remove
        # excess=2 items while protecting the latest target.
        # Oldest first: 20240101 (delete), 20240102 (skip/latest),
        # 20240103 (delete to reach excess=2).
        assert deleted == [
            "/mnt/dst/backup/snapshots/20240101T000000Z",
            "/mnt/dst/backup/snapshots/20240103T000000Z",
        ]
        mock_latest.assert_called_once()


class TestPruneSnapshotsRemote:
    @patch("nbkp.sync.snapshots.common.read_latest_symlink", return_value=None)
    @patch("nbkp.sync.snapshots.btrfs.run_remote_command")
    @patch("nbkp.sync.snapshots.common.run_remote_command")
    def test_prunes_oldest(
        self,
        mock_snap_rrc: MagicMock,
        mock_btrfs_rrc: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        shared_return = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240102T000000Z\n20240103T000000Z\n",
            stderr="",
        )
        mock_snap_rrc.return_value = shared_return
        mock_btrfs_rrc.return_value = shared_return
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        deleted = prune_snapshots(
            sync, config, max_snapshots=2, resolved_endpoints=resolved
        )
        assert deleted == [
            "/backup/data/snapshots/20240101T000000Z",
        ]
        # ls call (snapshots) + 1 × (property set + delete) calls (btrfs)
        assert mock_snap_rrc.call_count == 1
        assert mock_btrfs_rrc.call_count == 2

    @patch("nbkp.sync.snapshots.common.read_latest_symlink")
    @patch("nbkp.sync.snapshots.btrfs.run_remote_command")
    @patch("nbkp.sync.snapshots.common.run_remote_command")
    def test_protects_latest_snapshot(
        self,
        mock_snap_rrc: MagicMock,
        mock_btrfs_rrc: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        """The snapshot that latest points to must not be pruned."""
        shared_return = MagicMock(
            returncode=0,
            stdout=("20240101T000000Z\n20240102T000000Z\n20240103T000000Z\n"),
            stderr="",
        )
        mock_snap_rrc.return_value = shared_return
        mock_btrfs_rrc.return_value = shared_return
        # latest points to the oldest snapshot
        from nbkp.fsprotocol import Snapshot

        mock_latest.return_value = Snapshot.from_name("20240101T000000Z")
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        deleted = prune_snapshots(
            sync, config, max_snapshots=2, resolved_endpoints=resolved
        )
        # excess=1; 20240101T000000Z is skipped (latest), so
        # 20240102T000000Z is deleted instead.
        assert deleted == [
            "/backup/data/snapshots/20240102T000000Z",
        ]
        mock_latest.assert_called_once()
