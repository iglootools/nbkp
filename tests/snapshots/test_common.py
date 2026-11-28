"""Tests for nbkp.snapshots.common (shared snapshot helpers)."""

import errno
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import paramiko
import pytest
from pytest import MonkeyPatch  # noqa: PT013, TID251 -- (b) Path.symlink_to failing mid-update

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
)
from nbkp.remote.endpoints import ResolvedEndpoint
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.snapshots.common import (
    NoLatestSnapshot,
    create_snapshot_timestamp,
    list_snapshots,
    read_latest_link,
    read_latest_symlink,
    update_latest_symlink,
)
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.snapshots.hardlinks import (
    delete_snapshot as hl_delete_snapshot,
    prune_snapshots as hl_prune_snapshots,
)
from tests.fakes import FakeCommandRunner, completed
from tests.fixtures.config import two_volume_config


def _local_config() -> tuple[Config, SyncConfig]:
    sync, config = two_volume_config(
        dst_subdir="backup",
        dst_btrfs=BtrfsSnapshotConfig(enabled=True),
    )
    return config, sync


def _remote_config() -> tuple[Config, SyncConfig]:
    sync, config = two_volume_config(
        dst=RemoteVolume(
            slug="dst",
            ssh_endpoint="nas-server",
            path="/backup",
        ),
        ssh_endpoints=[
            SshEndpoint(
                slug="nas-server",
                host="nas.local",
                user="admin",
            ),
        ],
        dst_subdir="data",
        dst_btrfs=BtrfsSnapshotConfig(enabled=True),
    )
    return config, sync


def _remote_config_spaces() -> tuple[Config, SyncConfig]:
    sync, config = two_volume_config(
        src=LocalVolume(slug="src", path="/mnt/my src"),
        dst=RemoteVolume(
            slug="dst",
            ssh_endpoint="nas-server",
            path="/my backup",
        ),
        ssh_endpoints=[
            SshEndpoint(
                slug="nas-server",
                host="nas.local",
                user="admin",
            ),
        ],
        dst_subdir="my data",
        dst_btrfs=BtrfsSnapshotConfig(enabled=True),
    )
    return config, sync


def _hl_remote_config() -> tuple[SyncConfig, Config, dict[str, ResolvedEndpoint]]:
    server = SshEndpoint(slug="nas", host="nas.local", user="backup")
    sync, config = two_volume_config(
        src=LocalVolume(slug="src", path="/src"),
        dst=RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup"),
        ssh_endpoints=[server],
        dst_hard_link=HardLinkSnapshotConfig(
            enabled=True,
            max_snapshots=3,
        ),
    )
    resolved = {"dst": ResolvedEndpoint(server=server)}
    return sync, config, resolved


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


class TestListSnapshotsRemoteSpaces:
    def test_snapshots_listed_when_path_has_spaces(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            ),
        )
        config, sync = _remote_config_spaces()
        resolved = resolve_all_endpoints(config)

        result = list_snapshots(sync, config, resolved, run=runner)
        assert [s.name for s in result] == [
            "2024-01-01T00:00:00.000Z",
            "2024-01-15T12:00:00.000Z",
        ]
        assert runner.commands == [["ls", "/my backup/my data/snapshots"]]
        assert runner.calls[0].volume == config.volumes["dst"]


class TestListSnapshotsLocal:
    def test_snapshots_listed_when_present(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            ),
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner, resolved_endpoints={})
        assert [s.name for s in result] == [
            "2024-01-01T00:00:00.000Z",
            "2024-01-15T12:00:00.000Z",
        ]

    def test_none_listed_when_directory_empty(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=0, stdout=""))
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner, resolved_endpoints={})
        assert result == []

    def test_dir_missing(self) -> None:
        # ls fails, then `test -d` reports the directory absent
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=2, stdout="", stderr="No such file"),
                completed(returncode=1, stdout="", stderr=""),
            ],
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner, resolved_endpoints={})
        assert result == []


class TestListSnapshotsRemote:
    def test_snapshots_listed_when_present(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2024-01-01T00:00:00.000Z\n2024-01-15T12:00:00.000Z\n",
            ),
        )
        config, sync = _remote_config()
        resolved = resolve_all_endpoints(config)

        result = list_snapshots(sync, config, resolved, run=runner)
        assert [s.name for s in result] == [
            "2024-01-01T00:00:00.000Z",
            "2024-01-15T12:00:00.000Z",
        ]


def _hl_local_config(dst_path: Path) -> tuple[SyncConfig, Config]:
    return two_volume_config(
        src=LocalVolume(slug="src", path="/src"),
        dst=LocalVolume(slug="dst", path=str(dst_path)),
        dst_hard_link=HardLinkSnapshotConfig(enabled=True),
    )


class TestReadLatestSymlink:
    def test_snapshot_returned_when_latest_points_to_it(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path)
        latest = tmp_path / "latest"
        latest.symlink_to(f"snapshots/{_TS_LOCAL.name}")

        result = read_latest_symlink(sync, config, resolved_endpoints={})
        assert result == _TS_LOCAL

    def test_none_when_latest_missing(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path)

        result = read_latest_symlink(sync, config, resolved_endpoints={})
        assert result is None

    def test_remote_exists(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout=f"snapshots/{_TS_REMOTE.name}\n",
            ),
        )
        sync, config, resolved = _hl_remote_config()

        result = read_latest_symlink(
            sync,
            config,
            resolved_endpoints=resolved,
            run=runner,
        )
        assert result == _TS_REMOTE

    def test_remote_missing(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=1, stdout=""))
        sync, config, resolved = _hl_remote_config()

        result = read_latest_symlink(
            sync,
            config,
            resolved_endpoints=resolved,
            run=runner,
        )
        assert result is None

    def test_none_when_latest_is_dev_null(self, tmp_path: Path) -> None:
        """Latest -> /dev/null returns None."""
        sync, config = _hl_local_config(tmp_path)
        latest = tmp_path / "latest"
        latest.symlink_to("/dev/null")

        result = read_latest_symlink(sync, config, resolved_endpoints={})
        assert result is None

    def test_remote_devnull(self) -> None:
        """Remote latest -> /dev/null returns None."""
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="/dev/null\n",
            ),
        )
        sync, config, resolved = _hl_remote_config()

        result = read_latest_symlink(
            sync,
            config,
            resolved_endpoints=resolved,
            run=runner,
        )
        assert result is None


class TestReadLatestLink:
    """Unlike read_latest_symlink, it tells an absent link from /dev/null."""

    def test_remote_missing(self) -> None:
        runner = (
            FakeCommandRunner()
            .on("readlink", returns=completed(1))
            .on("test", "-L", returns=completed(1))
        )
        sync, config, resolved = _hl_remote_config()

        result = read_latest_link(sync, config, resolved_endpoints=resolved, run=runner)

        assert result is NoLatestSnapshot.ABSENT

    def test_remote_devnull(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(0, "/dev/null\n"))
        sync, config, resolved = _hl_remote_config()

        result = read_latest_link(sync, config, resolved_endpoints=resolved, run=runner)

        assert result is NoLatestSnapshot.NONE_YET

    def test_remote_snapshot(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(0, f"snapshots/{_TS_REMOTE.name}\n"),
        )
        sync, config, resolved = _hl_remote_config()

        result = read_latest_link(sync, config, resolved_endpoints=resolved, run=runner)

        assert result == _TS_REMOTE


class TestUpdateLatestSymlink:
    def test_latest_points_to_snapshot_when_local(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path)

        update_latest_symlink(sync, config, _TS_LOCAL, resolved_endpoints={})
        link = tmp_path / "latest"
        assert link.is_symlink()
        assert str(link.readlink()) == f"snapshots/{_TS_LOCAL.name}"

    def test_local_replaces_existing_without_leftovers(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path)
        link = tmp_path / "latest"
        link.symlink_to("/dev/null")

        update_latest_symlink(sync, config, _TS_LOCAL, resolved_endpoints={})

        assert str(link.readlink()) == f"snapshots/{_TS_LOCAL.name}"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["latest"]

    def test_local_failure_keeps_previous_latest(self, tmp_path: Path) -> None:
        """Latest is never missing, even when creating the new link fails."""
        sync, config = _hl_local_config(tmp_path)
        link = tmp_path / "latest"
        link.symlink_to("snapshots/previous")

        def fail(_self: Path, _target: str) -> None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

        with MonkeyPatch.context() as patch:
            patch.setattr(Path, "symlink_to", fail)
            with pytest.raises(
                SnapshotOperationError,
                check=_is_op(SnapshotOp.UPDATE_LATEST),
            ):
                update_latest_symlink(sync, config, _TS_LOCAL, resolved_endpoints={})
        assert str(link.readlink()) == "snapshots/previous"

    def test_latest_replaced_with_ln_when_remote(self) -> None:
        runner = FakeCommandRunner().on(returns=completed(returncode=0, stderr=""))
        sync, config, resolved = _hl_remote_config()

        update_latest_symlink(
            sync,
            config,
            _TS_REMOTE,
            resolved_endpoints=resolved,
            run=runner,
        )
        assert runner.commands == [
            ["ln", "-sfn", f"snapshots/{_TS_REMOTE.name}", "/backup/latest"],
        ]


def _is_op(op: SnapshotOp) -> Callable[[SnapshotOperationError], bool]:
    return lambda e: e.op is op


class TestListSnapshotsFailures:
    def test_unreadable_existing_dir_raises(self) -> None:
        """Ls failing on a directory that exists is not "no snapshots"."""
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=2, stdout="", stderr="Permission denied"),
                completed(returncode=0, stdout="", stderr=""),
            ],
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
            list_snapshots(sync, config, run=runner, resolved_endpoints={})

    def test_ignores_non_snapshot_entries(self) -> None:
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2026-03-01T10:00:00.000Z\nlost+found\n.DS_Store\nnotes.txt\n",
            ),
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner, resolved_endpoints={})
        assert [s.name for s in result] == ["2026-03-01T10:00:00.000Z"]

    def test_mixed_name_forms_are_sorted_by_time(self) -> None:
        """Linux (colon) and macOS (hyphen) names in one hour sort by time."""
        runner = FakeCommandRunner().on(
            returns=completed(
                returncode=0,
                stdout="2026-03-01T10-30-00.000Z\n2026-03-01T10:15:00.000Z\n",
            ),
        )
        config, sync = _local_config()

        result = list_snapshots(sync, config, run=runner, resolved_endpoints={})
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
            "readlink",
            returns=completed(0, "snapshots/2026-03-01T10-45-00.000Z\n"),
        )
        runner.on("rm", returns=completed(0))
        sync, config, resolved = _hl_remote_config()

        deleted = hl_prune_snapshots(
            sync,
            config,
            2,
            resolved_endpoints=resolved,
            run=runner,
        )
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
        """Readlink failing on an existing symlink must not read as "absent"."""
        runner = FakeCommandRunner().on(
            returns=[
                completed(returncode=1, stdout="", stderr="I/O error"),
                completed(returncode=0, stdout="", stderr=""),  # test -L: exists
            ],
        )
        sync, config, resolved = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=_is_op(SnapshotOp.READ_LATEST),
        ):
            read_latest_symlink(sync, config, resolved_endpoints=resolved, run=runner)

    def test_target_not_a_snapshot_raises(self) -> None:
        """A latest target nbkp did not write is an error, not a guess."""
        runner = FakeCommandRunner().on(
            "readlink",
            returns=completed(0, "snapshots/2026-03-07T09:00:00\n"),
        )
        sync, config, resolved = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=_is_op(SnapshotOp.READ_LATEST),
        ):
            read_latest_symlink(sync, config, resolved_endpoints=resolved, run=runner)

    def test_non_snapshot_target_raises(self) -> None:
        """A target that is not a snapshot name is a failed read, not a crash."""
        runner = FakeCommandRunner().on(
            "readlink",
            returns=completed(0, "snapshots/foo\n"),
        )
        sync, config, resolved = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: e.op is SnapshotOp.READ_LATEST and "foo" in e.stderr,
        ):
            read_latest_symlink(sync, config, resolved_endpoints=resolved, run=runner)

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
        sync, config, resolved = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=_is_op(SnapshotOp.READ_LATEST),
        ):
            read_latest_symlink(sync, config, resolved_endpoints=resolved, run=runner)

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
            ],
        )
        sync, config, resolved = _hl_remote_config()

        with pytest.raises(
            SnapshotOperationError,
            check=_is_op(SnapshotOp.READ_LATEST),
        ):
            hl_prune_snapshots(sync, config, 3, resolved_endpoints=resolved, run=runner)
        assert len(runner.calls) == 3  # no rm -rf issued


class TestLocalOSErrorTranslation:
    def test_update_latest_symlink_missing_dir(self, tmp_path: Path) -> None:
        sync, config = _hl_local_config(tmp_path / "absent")

        with pytest.raises(
            SnapshotOperationError,
            check=_is_op(SnapshotOp.UPDATE_LATEST),
        ):
            update_latest_symlink(sync, config, _TS_LOCAL, resolved_endpoints={})

    def test_hard_link_delete_missing_dir(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="dst", path=str(tmp_path))
        with pytest.raises(SnapshotOperationError, check=_is_op(SnapshotOp.DELETE)):
            hl_delete_snapshot(str(tmp_path / "snapshots" / "missing"), vol, {})
