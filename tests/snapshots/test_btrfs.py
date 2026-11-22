from datetime import UTC, datetime

import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.snapshots.btrfs import (
    create_snapshot,
    delete_snapshot,
    prune_snapshots,
)
from nbkp.snapshots.common import create_snapshot_timestamp
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
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


_NOW = datetime(2024, 1, 15, 12, 0, 0, 0, tzinfo=UTC)
_DENIED = completed(1, stderr="permission denied")
_THREE = (
    "2024-01-01T00:00:00.000Z\n2024-01-02T00:00:00.000Z\n2024-01-03T00:00:00.000Z\n"
)


class TestCreateSnapshot:
    def test_local(self) -> None:
        config, sync = _local_config()
        runner = FakeCommandRunner()

        expected_ts = create_snapshot_timestamp(_NOW, config.volumes["dst"], "linux")
        path = create_snapshot(sync, config, now=_NOW, platform="linux", run=runner)

        assert path == f"/mnt/dst/backup/snapshots/{expected_ts.name}"
        assert runner.commands == [
            [
                "btrfs",
                "subvolume",
                "snapshot",
                "-r",
                "/mnt/dst/backup/staging",
                f"/mnt/dst/backup/snapshots/{expected_ts.name}",
            ],
        ]
        assert runner.calls[0].volume == config.volumes["dst"]

    def test_failure(self) -> None:
        config, sync = _local_config()
        runner = FakeCommandRunner().on("btrfs", returns=_DENIED)

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: e.op is SnapshotOp.CREATE,
        ):
            create_snapshot(sync, config, now=_NOW, platform="linux", run=runner)

    def test_remote(self) -> None:
        config, sync = _remote_config()
        runner = FakeCommandRunner()

        path = create_snapshot(sync, config, now=_NOW, platform="linux", run=runner)

        assert path == "/backup/data/snapshots/2024-01-15T12:00:00.000Z"
        assert runner.commands == [
            [
                "btrfs",
                "subvolume",
                "snapshot",
                "-r",
                "/backup/data/staging",
                "/backup/data/snapshots/2024-01-15T12:00:00.000Z",
            ],
        ]
        assert runner.calls[0].volume == config.volumes["dst"]

    def test_local_paths_with_spaces(self) -> None:
        config, sync = _local_config_spaces()
        runner = FakeCommandRunner()

        expected_ts = create_snapshot_timestamp(_NOW, config.volumes["dst"], "linux")
        path = create_snapshot(sync, config, now=_NOW, platform="linux", run=runner)

        assert path == f"/mnt/my dst/my backup/snapshots/{expected_ts.name}"
        assert runner.commands == [
            [
                "btrfs",
                "subvolume",
                "snapshot",
                "-r",
                "/mnt/my dst/my backup/staging",
                f"/mnt/my dst/my backup/snapshots/{expected_ts.name}",
            ],
        ]

    def test_remote_paths_with_spaces(self) -> None:
        config, sync = _remote_config_spaces()
        runner = FakeCommandRunner()

        path = create_snapshot(sync, config, now=_NOW, platform="linux", run=runner)

        assert path == "/my backup/my data/snapshots/2024-01-15T12:00:00.000Z"
        assert runner.commands == [
            [
                "btrfs",
                "subvolume",
                "snapshot",
                "-r",
                "/my backup/my data/staging",
                "/my backup/my data/snapshots/2024-01-15T12:00:00.000Z",
            ],
        ]


class TestDeleteSnapshot:
    _PATH = "/mnt/dst/backup/snapshots/2024-01-01T00:00:00.000Z"

    def test_makes_writable_then_deletes(self) -> None:
        config, _ = _local_config()
        runner = FakeCommandRunner()

        delete_snapshot(self._PATH, config.volumes["dst"], {}, run=runner)

        assert runner.commands == [
            ["btrfs", "property", "set", self._PATH, "ro", "false"],
            ["btrfs", "subvolume", "delete", self._PATH],
        ]

    def test_failure_on_property_set(self) -> None:
        config, _ = _local_config()
        runner = FakeCommandRunner().on("btrfs", "property", returns=_DENIED)

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: e.op is SnapshotOp.MAKE_WRITABLE,
        ):
            delete_snapshot(self._PATH, config.volumes["dst"], {}, run=runner)
        assert runner.commands_starting_with("btrfs", "subvolume") == []

    def test_failed_delete_makes_the_snapshot_read_only_again(self) -> None:
        config, _ = _local_config()
        runner = FakeCommandRunner().on("btrfs", "subvolume", returns=_DENIED)

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: (
                e.op is SnapshotOp.DELETE
                and e.stderr
                == "permission denied; the snapshot was made read-only again"
            ),
        ):
            delete_snapshot(self._PATH, config.volumes["dst"], {}, run=runner)
        assert runner.commands == [
            ["btrfs", "property", "set", self._PATH, "ro", "false"],
            ["btrfs", "subvolume", "delete", self._PATH],
            ["btrfs", "property", "set", self._PATH, "ro", "true"],
        ]

    def test_failed_delete_reports_a_snapshot_left_writable(self) -> None:
        config, _ = _local_config()
        runner = (
            FakeCommandRunner()
            .on("btrfs", "subvolume", returns=_DENIED)
            .on(
                "btrfs",
                "property",
                returns=[completed(), completed(1, stderr="read-only filesystem")],
            )
        )

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: (
                e.op is SnapshotOp.DELETE
                and e.stderr.endswith("so it is left writable: read-only filesystem")
            ),
        ):
            delete_snapshot(self._PATH, config.volumes["dst"], {}, run=runner)

    def test_connection_lost_while_restoring_keeps_the_delete_error(self) -> None:
        config, _ = _local_config()
        runner = (
            FakeCommandRunner()
            .on("btrfs", "subvolume", returns=_DENIED)
            .on(
                "btrfs",
                "property",
                returns=[completed(), ConnectionResetError("connection reset")],
            )
        )

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: (
                e.op is SnapshotOp.DELETE
                and e.stderr.startswith("permission denied;")
                and e.stderr.endswith("left writable: connection reset")
            ),
        ):
            delete_snapshot(self._PATH, config.volumes["dst"], {}, run=runner)


def _listing(stdout: str, latest: str | None = None) -> FakeCommandRunner:
    """Script a remote destination listing *stdout*, with ``latest`` → *latest*."""
    readlink = completed(0, f"snapshots/{latest}\n") if latest else completed(1)
    return (
        FakeCommandRunner()
        .on("ls", returns=completed(0, stdout))
        .on("readlink", returns=readlink)
        .on("test", "-L", returns=completed(1))
    )


def _deleted(runner: FakeCommandRunner) -> list[str]:
    return [
        c[-1] for c in runner.commands_starting_with("btrfs", "subvolume", "delete")
    ]


class TestPruneSnapshots:
    def test_prunes_oldest(self) -> None:
        config, sync = _remote_config()
        runner = _listing(_THREE)

        deleted = prune_snapshots(sync, config, max_snapshots=1, run=runner)

        assert deleted == [
            "/backup/data/snapshots/2024-01-01T00:00:00.000Z",
            "/backup/data/snapshots/2024-01-02T00:00:00.000Z",
        ]
        assert _deleted(runner) == deleted

    def test_nothing_to_prune(self) -> None:
        config, sync = _remote_config()
        runner = _listing(_THREE)

        deleted = prune_snapshots(sync, config, max_snapshots=5, run=runner)

        assert deleted == []
        assert runner.commands == [["ls", "/backup/data/snapshots"]]

    def test_dry_run(self) -> None:
        config, sync = _remote_config()
        runner = _listing(_THREE)

        deleted = prune_snapshots(
            sync,
            config,
            max_snapshots=1,
            dry_run=True,
            run=runner,
        )

        assert deleted == [
            "/backup/data/snapshots/2024-01-01T00:00:00.000Z",
            "/backup/data/snapshots/2024-01-02T00:00:00.000Z",
        ]
        assert runner.commands_starting_with("btrfs") == []

    def test_protects_latest_snapshot(self) -> None:
        """The snapshot that latest points to must not be pruned."""
        config, sync = _remote_config()
        runner = _listing(_THREE, latest="2024-01-02T00:00:00.000Z")

        deleted = prune_snapshots(sync, config, max_snapshots=1, run=runner)

        # excess=2: 20240102 (latest) is skipped, so the two others go.
        assert deleted == [
            "/backup/data/snapshots/2024-01-01T00:00:00.000Z",
            "/backup/data/snapshots/2024-01-03T00:00:00.000Z",
        ]
        assert _deleted(runner) == deleted

    def test_protects_latest_when_oldest(self) -> None:
        config, sync = _remote_config()
        runner = _listing(_THREE, latest="2024-01-01T00:00:00.000Z")

        deleted = prune_snapshots(sync, config, max_snapshots=2, run=runner)

        assert deleted == ["/backup/data/snapshots/2024-01-02T00:00:00.000Z"]
