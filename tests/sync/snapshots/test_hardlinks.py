"""Tests for nbkp.sync.snapshots.hardlinks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoint,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.sync.snapshots.common import create_snapshot_timestamp
from nbkp.sync.snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot,
    prune_snapshots,
)

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
_LOCAL_VOL = LocalVolume(slug="dummy", path="/dummy")
_REMOTE_VOL = RemoteVolume(slug="dummy", ssh_endpoint="dummy", path="/dummy")
_TS_LOCAL = create_snapshot_timestamp(_NOW, _LOCAL_VOL)
_TS_REMOTE = create_snapshot_timestamp(_NOW, _REMOTE_VOL)


def _local_config() -> tuple[SyncConfig, Config]:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
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
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=5
                ),
            ),
        },
        syncs={"s1": sync},
    )
    return sync, config


def _remote_config() -> tuple[SyncConfig, Config, dict[str, ResolvedEndpoint]]:
    server = SshEndpoint(slug="nas", host="nas.local", user="backup")
    src = LocalVolume(slug="src", path="/src")
    dst = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        ssh_endpoints={"nas": server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=3
                ),
            ),
        },
        syncs={"s1": sync},
    )
    re = {"dst": ResolvedEndpoint(server=server)}
    return sync, config, re


class TestCreateSnapshotDir:
    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_local(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        sync, config = _local_config()

        path = create_snapshot_dir(sync, config, now=_NOW)

        assert path == f"/dst/snapshots/{_TS_LOCAL.name}"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["mkdir", "-p", f"/dst/snapshots/{_TS_LOCAL.name}"]

    @patch("nbkp.sync.snapshots.common.run_remote_command")
    def test_remote(self, mock_remote: MagicMock) -> None:
        mock_remote.return_value = MagicMock(returncode=0, stderr="")
        sync, config, re = _remote_config()

        path = create_snapshot_dir(sync, config, now=_NOW, resolved_endpoints=re)

        assert path == f"/backup/snapshots/{_TS_REMOTE.name}"
        mock_remote.assert_called_once()

    @patch("nbkp.sync.snapshots.common.subprocess.run")
    def test_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
        sync, config = _local_config()

        with pytest.raises(RuntimeError, match="mkdir"):
            create_snapshot_dir(sync, config, now=_NOW)


class TestCleanupOrphanedSnapshots:
    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_deletes_orphans(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        # Create snapshots dir and symlink
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "2024-01-01T00:00:00.000Z").mkdir()
        (snaps / "2024-01-02T00:00:00.000Z").mkdir()  # orphan
        latest = tmp_path / "latest"
        latest.symlink_to("snapshots/2024-01-01T00:00:00.000Z")

        mock_list.return_value = [
            Snapshot.from_name("2024-01-01T00:00:00.000Z"),
            Snapshot.from_name("2024-01-02T00:00:00.000Z"),
        ]

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert len(deleted) == 1
        assert "2024-01-02T00:00:00.000Z" in deleted[0]
        assert not (snaps / "2024-01-02T00:00:00.000Z").exists()

    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_no_orphans(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )
        latest = tmp_path / "latest"
        latest.symlink_to("snapshots/2024-01-02T00:00:00.000Z")

        mock_list.return_value = [
            Snapshot.from_name("2024-01-01T00:00:00.000Z"),
            Snapshot.from_name("2024-01-02T00:00:00.000Z"),
        ]

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert deleted == []

    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_no_latest_symlink(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert deleted == []
        mock_list.assert_not_called()


class TestDeleteSnapshot:
    def test_local(self, tmp_path: Path) -> None:
        snap = tmp_path / "snap1"
        snap.mkdir()
        (snap / "file.txt").write_text("data")
        vol = LocalVolume(slug="dst", path=str(tmp_path))

        delete_snapshot(str(snap), vol, {})
        assert not snap.exists()

    @patch("nbkp.sync.snapshots.common.run_remote_command")
    def test_remote(self, mock_remote: MagicMock) -> None:
        mock_remote.return_value = MagicMock(returncode=0, stderr="")
        server = SshEndpoint(slug="nas", host="nas.local", user="backup")
        vol = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup")
        re = {"dst": ResolvedEndpoint(server=server)}

        delete_snapshot("/backup/snapshots/T1", vol, re)
        mock_remote.assert_called_once()
        cmd = mock_remote.call_args[0][1]
        assert cmd == ["rm", "-rf", "/backup/snapshots/T1"]


_S1 = "2024-01-01T00:00:00.000Z"
_S2 = "2024-01-02T00:00:00.000Z"
_S3 = "2024-01-03T00:00:00.000Z"


class TestPruneSnapshots:
    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_prune_excess(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True, max_snapshots=2
                    ),
                ),
            },
            syncs={"s1": sync},
        )

        # Create snapshots + latest symlink
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        for name in [_S1, _S2, _S3]:
            (snaps / name).mkdir()
        latest = tmp_path / "latest"
        latest.symlink_to(f"snapshots/{_S3}")

        mock_list.return_value = [
            Snapshot.from_name(_S1),
            Snapshot.from_name(_S2),
            Snapshot.from_name(_S3),
        ]

        deleted = prune_snapshots(sync, config, 2)
        assert len(deleted) == 1
        assert _S1 in deleted[0]

    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_never_prunes_latest(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True, max_snapshots=1
                    ),
                ),
            },
            syncs={"s1": sync},
        )

        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        for name in [_S1, _S2]:
            (snaps / name).mkdir()
        latest = tmp_path / "latest"
        latest.symlink_to(f"snapshots/{_S1}")

        mock_list.return_value = [
            Snapshot.from_name(_S1),
            Snapshot.from_name(_S2),
        ]

        # max_snapshots=1, but _S1 is latest so only _S2 pruned
        deleted = prune_snapshots(sync, config, 1)
        assert len(deleted) == 1
        assert _S2 in deleted[0]
        assert (snaps / _S1).exists()

    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_dry_run(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        for name in [_S1, _S2, _S3]:
            (snaps / name).mkdir()
        latest = tmp_path / "latest"
        latest.symlink_to(f"snapshots/{_S3}")

        mock_list.return_value = [
            Snapshot.from_name(_S1),
            Snapshot.from_name(_S2),
            Snapshot.from_name(_S3),
        ]

        deleted = prune_snapshots(sync, config, 1, dry_run=True)
        assert len(deleted) == 2
        # Dry run: directories still exist
        assert (snaps / _S1).exists()
        assert (snaps / _S2).exists()

    @patch("nbkp.sync.snapshots.hardlinks.list_snapshots")
    def test_no_excess(self, mock_list: MagicMock, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
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
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        mock_list.return_value = [
            Snapshot.from_name(_S1),
        ]

        deleted = prune_snapshots(sync, config, 5)
        assert deleted == []
