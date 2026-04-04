"""Tests for nbkp.snapshots.common (shared snapshot helpers)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.config.epresolution import ResolvedEndpoint
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.snapshots.common import (
    create_snapshot_timestamp,
    get_latest_snapshot,
    list_snapshots,
    read_latest_symlink,
    update_latest_symlink,
)


# ── Config helpers ───────────────────────────────────────────


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


def _hl_local_config() -> tuple[SyncConfig, Config]:
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


def _hl_remote_config() -> tuple[SyncConfig, Config, dict[str, ResolvedEndpoint]]:
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


_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
_LOCAL_VOL = LocalVolume(slug="dummy", path="/dummy")
_REMOTE_VOL = RemoteVolume(slug="dummy", ssh_endpoint="dummy", path="/dummy")
_TS_LOCAL = create_snapshot_timestamp(_NOW, _LOCAL_VOL)
_TS_REMOTE = create_snapshot_timestamp(_NOW, _REMOTE_VOL)


# ── create_snapshot_timestamp ────────────────────────────────


class TestCreateSnapshotTimestamp:
    def test_remote_volume_uses_colons(self) -> None:
        vol = RemoteVolume(slug="r", ssh_endpoint="s", path="/p")
        result = create_snapshot_timestamp(_NOW, vol, platform="linux")
        assert result.name == "2026-02-21T12:00:00.000Z"

    def test_local_volume_darwin_uses_hyphens(self) -> None:
        vol = LocalVolume(slug="l", path="/p")
        result = create_snapshot_timestamp(_NOW, vol, platform="darwin")
        assert result.name == "2026-02-21T12-00-00.000Z"

    def test_local_volume_linux_uses_colons(self) -> None:
        vol = LocalVolume(slug="l", path="/p")
        result = create_snapshot_timestamp(_NOW, vol, platform="linux")
        assert result.name == "2026-02-21T12:00:00.000Z"


# ── get_latest_snapshot ──────────────────────────────────────


class TestGetLatestSnapshotLocal:
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240115T120000Z\n",
        )
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config)
        assert result is not None
        assert result.name == "20240115T120000Z"

    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_empty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config)
        assert result is None

    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_dir_missing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=2, stdout="")
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config)
        assert result is None


class TestGetLatestSnapshotRemote:
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240115T120000Z\n",
        )
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        result = get_latest_snapshot(sync, config, resolved)
        assert result is not None
        assert result.name == "20240115T120000Z"
        mock_run.assert_called_once_with(
            config.ssh_endpoints["nas-server"],
            ["ls", "/backup/data/snapshots"],
            [],
        )


class TestGetLatestSnapshotRemoteSpaces:
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240115T120000Z\n",
        )
        config, sync = _remote_config_spaces()
        resolved = resolve_all_endpoints(config)

        result = get_latest_snapshot(sync, config, resolved)
        assert result is not None
        assert result.name == "20240115T120000Z"
        mock_run.assert_called_once_with(
            config.ssh_endpoints["nas-server"],
            ["ls", "/my backup/my data/snapshots"],
            [],
        )


# ── list_snapshots ───────────────────────────────────────────


class TestListSnapshotsLocal:
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240115T120000Z\n",
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config)
        assert [s.name for s in result] == [
            "20240101T000000Z",
            "20240115T120000Z",
        ]

    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_empty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        config, sync = _local_config()

        result = list_snapshots(sync, config)
        assert result == []

    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_dir_missing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=2, stdout="")
        config, sync = _local_config()

        result = list_snapshots(sync, config)
        assert result == []


class TestListSnapshotsRemote:
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="20240101T000000Z\n20240115T120000Z\n",
        )
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        result = list_snapshots(sync, config, resolved)
        assert [s.name for s in result] == [
            "20240101T000000Z",
            "20240115T120000Z",
        ]


# ── read_latest_symlink ─────────────────────────────────────


class TestReadLatestSymlink:
    def test_local_exists(self, tmp_path: Path) -> None:
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
        latest.symlink_to(f"snapshots/{_TS_LOCAL.name}")

        result = read_latest_symlink(sync, config)
        assert result == _TS_LOCAL

    def test_local_missing(self, tmp_path: Path) -> None:
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

        result = read_latest_symlink(sync, config)
        assert result is None

    @patch("nbkp.snapshots.common.run_remote_command")
    def test_remote_exists(self, mock_remote: MagicMock) -> None:
        mock_remote.return_value = MagicMock(
            returncode=0,
            stdout=f"snapshots/{_TS_REMOTE.name}\n",
        )
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re)
        assert result == _TS_REMOTE

    @patch("nbkp.snapshots.common.run_remote_command")
    def test_remote_missing(self, mock_remote: MagicMock) -> None:
        mock_remote.return_value = MagicMock(returncode=1, stdout="")
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re)
        assert result is None

    def test_local_devnull(self, tmp_path: Path) -> None:
        """latest -> /dev/null returns None."""
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
        latest.symlink_to("/dev/null")

        result = read_latest_symlink(sync, config)
        assert result is None

    @patch("nbkp.snapshots.common.run_remote_command")
    def test_remote_devnull(self, mock_remote: MagicMock) -> None:
        """Remote latest -> /dev/null returns None."""
        mock_remote.return_value = MagicMock(
            returncode=0,
            stdout="/dev/null\n",
        )
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re)
        assert result is None


# ── update_latest_symlink ────────────────────────────────────


class TestUpdateLatestSymlink:
    def test_local(self, tmp_path: Path) -> None:
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

        update_latest_symlink(sync, config, _TS_LOCAL)
        link = tmp_path / "latest"
        assert link.is_symlink()
        assert str(link.readlink()) == f"snapshots/{_TS_LOCAL.name}"

    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_remote(self, mock_remote: MagicMock) -> None:
        mock_remote.return_value = MagicMock(returncode=0, stderr="")
        sync, config, re = _hl_remote_config()

        update_latest_symlink(sync, config, _TS_REMOTE, resolved_endpoints=re)
        mock_remote.assert_called_once()
        cmd = mock_remote.call_args[0][1]
        assert "ln" in cmd
        assert f"snapshots/{_TS_REMOTE.name}" in cmd
