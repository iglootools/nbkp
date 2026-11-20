"""Tests for nbkp.snapshots.common (shared snapshot helpers)."""

from datetime import UTC, datetime
from pathlib import Path

import paramiko
import pytest
from pytest import MonkeyPatch  # noqa: TID251 -- (b) Path.symlink_to failing mid-update

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
from nbkp.remote.endpoints import ResolvedEndpoint
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.snapshots.common import (
    create_snapshot_timestamp,
    get_latest_snapshot,
    list_snapshots,
    read_latest_symlink,
    update_latest_symlink,
)
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.snapshots.hardlinks import (
    delete_snapshot as hl_delete_snapshot,
    prune_snapshots as hl_prune_snapshots,
)
from tests.fakes import FakeCommandRunner, completed


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


_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)
_LOCAL_VOL = LocalVolume(slug="dummy", path="/dummy")
_REMOTE_VOL = RemoteVolume(slug="dummy", ssh_endpoint="dummy", path="/dummy")
_TS_LOCAL = create_snapshot_timestamp(_NOW, _LOCAL_VOL, "linux")
_TS_REMOTE = create_snapshot_timestamp(_NOW, _REMOTE_VOL, "linux")


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


class TestGetLatestSnapshotLocal:
    def test_found(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            )
        )
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config, run=runner)
        assert result is not None
        assert result.name == "2024-01-15T12:00:00.000Z"

    def test_empty(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=0, stdout=""))
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config, run=runner)
        assert result is None

    def test_dir_missing(self) -> None:
        # ls fails, then `test -d` reports the directory absent
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=2, stdout="", stderr="No such file"),
                completed(returncode=1, stdout="", stderr=""),
            ]
        )
        config, sync = _local_config()

        result = get_latest_snapshot(sync, config, run=runner)
        assert result is None


class TestGetLatestSnapshotRemote:
    def test_found(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            )
        )
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        result = get_latest_snapshot(sync, config, resolved, run=runner)
        assert result is not None
        assert result.name == "2024-01-15T12:00:00.000Z"
        assert runner.commands == [["ls", "/backup/data/snapshots"]]
        assert runner.calls[0].volume == config.volumes["dst"]


class TestGetLatestSnapshotRemoteSpaces:
    def test_found(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            )
        )
        config, sync = _remote_config_spaces()
        resolved = resolve_all_endpoints(config)

        result = get_latest_snapshot(sync, config, resolved, run=runner)
        assert result is not None
        assert result.name == "2024-01-15T12:00:00.000Z"
        assert runner.commands == [["ls", "/my backup/my data/snapshots"]]
        assert runner.calls[0].volume == config.volumes["dst"]


class TestListSnapshotsLocal:
    def test_found(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            )
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner)
        assert [s.name for s in result] == [
            "2024-01-01T00:00:00.000Z",
            "2024-01-15T12:00:00.000Z",
        ]

    def test_empty(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=0, stdout=""))
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner)
        assert result == []

    def test_dir_missing(self) -> None:
        # ls fails, then `test -d` reports the directory absent
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=2, stdout="", stderr="No such file"),
                completed(returncode=1, stdout="", stderr=""),
            ]
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner)
        assert result == []


class TestListSnapshotsRemote:
    def test_found(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            )
        )
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        result = list_snapshots(sync, config, resolved, run=runner)
        assert [s.name for s in result] == [
            "2024-01-01T00:00:00.000Z",
            "2024-01-15T12:00:00.000Z",
        ]


def _hl_local_config(dst_path: Path) -> tuple[SyncConfig, Config]:
    config = Config(
        volumes={
            "src": LocalVolume(slug="src", path="/src"),
            "dst": LocalVolume(slug="dst", path=str(dst_path)),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst")},
    )
    return config.syncs["s1"], config


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

    def test_remote_exists(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout=f"snapshots/{_TS_REMOTE.name}\n",
            )
        )
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)
        assert result == _TS_REMOTE

    def test_remote_missing(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=1, stdout=""))
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)
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

    def test_remote_devnull(self) -> None:
        """Remote latest -> /dev/null returns None."""
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="/dev/null\n",
            )
        )
        sync, config, re = _hl_remote_config()

        result = read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)
        assert result is None


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

    def test_local_replaces_existing_without_leftovers(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path)
        link = tmp_path / "latest"
        link.symlink_to("/dev/null")

        update_latest_symlink(sync, config, _TS_LOCAL)

        assert str(link.readlink()) == f"snapshots/{_TS_LOCAL.name}"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["latest"]

    def test_local_failure_keeps_previous_latest(self, tmp_path: Path) -> None:
        """latest is never missing, even when creating the new link fails."""
        sync, config = _hl_local_config(tmp_path)
        link = tmp_path / "latest"
        link.symlink_to("snapshots/previous")

        def fail(self: Path, target: str) -> None:
            raise OSError("disk full")

        with (
            MonkeyPatch.context() as patch,
            pytest.raises(
                SnapshotOperationError, check=_is_op(SnapshotOp.UPDATE_LATEST)
            ),
        ):
            patch.setattr(Path, "symlink_to", fail)
            update_latest_symlink(sync, config, _TS_LOCAL)
        assert str(link.readlink()) == "snapshots/previous"

    def test_remote(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=0, stderr=""))
        sync, config, re = _hl_remote_config()

        update_latest_symlink(
            sync, config, _TS_REMOTE, resolved_endpoints=re, run=runner
        )
        assert runner.commands == [
            ["ln", "-sfn", f"snapshots/{_TS_REMOTE.name}", "/backup/latest"]
        ]


def _is_op(op: SnapshotOp):
    return lambda e: e.op is op


class TestListSnapshotsFailures:
    def test_unreadable_existing_dir_raises(self) -> None:
        """ls failing on a directory that exists is not "no snapshots"."""
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=2, stdout="", stderr="Permission denied"),
                completed(returncode=0, stdout="", stderr=""),
            ]
        )
        config, sync = _local_config()

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: (
                e.op is SnapshotOp.LIST
                and e.path == "/mnt/dst/backup/snapshots"
                and e.stderr == "Permission denied"
            ),
        ):
            list_snapshots(sync, config, run=runner)

    def test_ignores_non_snapshot_entries(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2026-03-01T10:00:00.000Z\nlost+found\n.DS_Store\nnotes.txt\n",
            )
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner)
        assert [s.name for s in result] == ["2026-03-01T10:00:00.000Z"]

    def test_mixed_name_forms_are_sorted_by_time(self) -> None:
        """Linux (colon) and macOS (hyphen) names in one hour sort by time."""
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2026-03-01T10-30-00.000Z\n2026-03-01T10:15:00.000Z\n",
            )
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner)
        assert [s.name for s in result] == [
            "2026-03-01T10:15:00.000Z",
            "2026-03-01T10-30-00.000Z",
        ]

    def test_prune_deletes_the_oldest_when_name_forms_are_mixed(self) -> None:
        runner = FakeCommandRunner().on(
            "ls",
            returns=completed(
                0,
                "2026-03-01T10-30-00.000Z\n2026-03-01T10:15:00.000Z\n"
                "2026-03-01T10-45-00.000Z\n",
            ),
        )
        runner.on(
            "readlink", returns=completed(0, "snapshots/2026-03-01T10-45-00.000Z\n")
        )
        runner.on("rm", returns=completed(0))
        sync, config, re = _hl_remote_config()

        deleted = hl_prune_snapshots(sync, config, 2, resolved_endpoints=re, run=runner)
        assert [p.rsplit("/", 1)[-1] for p in deleted] == ["2026-03-01T10:15:00.000Z"]

    @pytest.mark.parametrize(
        "transport_error",
        [
            paramiko.SSHException("connection reset"),
            EOFError("connection reset during handshake"),
        ],
    )
    def test_transport_error_is_translated(self, transport_error: Exception) -> None:
        runner = FakeCommandRunner().on(returns=transport_error)
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: e.op is SnapshotOp.LIST and "reset" in e.stderr,
        ):
            list_snapshots(sync, config, resolved, run=runner)


class TestReadLatestSymlinkFailures:
    def test_remote_unreadable_symlink_raises(self) -> None:
        """readlink failing on an existing symlink must not read as "absent"."""
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=1, stdout="", stderr="I/O error"),
                completed(returncode=0, stdout="", stderr=""),  # test -L: exists
            ]
        )
        sync, config, re = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError, check=_is_op(SnapshotOp.READ_LATEST)
        ):
            read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)

    def test_target_not_a_snapshot_raises(self) -> None:
        """A latest target nbkp did not write is an error, not a guess."""
        runner = FakeCommandRunner().on(
            "readlink", returns=completed(0, "snapshots/2026-03-07T09:00:00\n")
        )
        sync, config, re = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError, check=_is_op(SnapshotOp.READ_LATEST)
        ):
            read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)

    def test_non_snapshot_target_raises(self) -> None:
        """A target that is not a snapshot name is a failed read, not a crash."""
        runner = FakeCommandRunner().on(
            "readlink", returns=completed(0, "snapshots/foo\n")
        )
        sync, config, re = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: e.op is SnapshotOp.READ_LATEST and "foo" in e.stderr,
        ):
            read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)

    @pytest.mark.parametrize(
        "target",
        [
            "../other/snapshots/2026-03-07T09:00:00.000Z",
            "/mnt/elsewhere/snapshots/2026-03-07T09:00:00.000Z",
            "2026-03-07T09:00:00.000Z",
        ],
    )
    def test_target_outside_snapshots_dir_raises(self, target: str) -> None:
        """Only ``snapshots/<name>`` designates one of this endpoint's snapshots."""
        runner = FakeCommandRunner().on("readlink", returns=completed(0, f"{target}\n"))
        sync, config, re = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError, check=_is_op(SnapshotOp.READ_LATEST)
        ):
            read_latest_symlink(sync, config, resolved_endpoints=re, run=runner)

    def test_prune_does_not_delete_when_latest_unknown(self) -> None:
        """An unreadable latest disables pruning instead of its protection."""
        runner = FakeCommandRunner().on(
            returns=[
                completed(
                    returncode=0,
                    stdout="".join(
                        f"2026-03-0{i}T10:00:00.000Z\n" for i in range(1, 6)
                    ),
                    stderr="",
                ),
                completed(returncode=1, stdout="", stderr="I/O error"),  # readlink
                completed(returncode=2, stdout="", stderr=""),  # test -L: error
            ]
        )
        sync, config, re = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError, check=_is_op(SnapshotOp.READ_LATEST)
        ):
            hl_prune_snapshots(sync, config, 3, resolved_endpoints=re, run=runner)
        assert len(runner.calls) == 3  # no rm -rf issued


class TestLocalOSErrorTranslation:
    def test_update_latest_symlink_missing_dir(self, tmp_path: Path) -> None:
        dst = LocalVolume(slug="dst", path=str(tmp_path / "absent"))
        src = LocalVolume(slug="src", path="/src")
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
            syncs={"s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst")},
        )
        sync = config.syncs["s1"]

        with pytest.raises(
            SnapshotOperationError, check=_is_op(SnapshotOp.UPDATE_LATEST)
        ):
            update_latest_symlink(sync, config, _TS_LOCAL)

    def test_hard_link_delete_missing_dir(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="dst", path=str(tmp_path))
        with pytest.raises(SnapshotOperationError, check=_is_op(SnapshotOp.DELETE)):
            hl_delete_snapshot(str(tmp_path / "snapshots" / "missing"), vol, {})
