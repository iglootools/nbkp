from datetime import UTC, datetime
from pathlib import Path

import pytest

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.endpoints import ResolvedEndpoint
from nbkp.snapshots.common import create_snapshot_timestamp
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot,
    prune_snapshots,
)
from tests.fakes import FakeCommandRunner, completed

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)
_LOCAL_VOL = LocalVolume(slug="dummy", path="/dummy")
_REMOTE_VOL = RemoteVolume(slug="dummy", ssh_endpoint="dummy", path="/dummy")
_TS_LOCAL = create_snapshot_timestamp(_NOW, _LOCAL_VOL, "linux")
_TS_REMOTE = create_snapshot_timestamp(_NOW, _REMOTE_VOL, "linux")


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
                    enabled=True,
                    max_snapshots=5,
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
                    enabled=True,
                    max_snapshots=3,
                ),
            ),
        },
        syncs={"s1": sync},
    )
    re = {"dst": ResolvedEndpoint(server=server)}
    return sync, config, re


class TestCreateSnapshotDir:
    def test_local(self) -> None:
        sync, config = _local_config()
        runner = FakeCommandRunner()

        path = create_snapshot_dir(sync, config, now=_NOW, platform="linux", run=runner)

        assert path == f"/dst/snapshots/{_TS_LOCAL.name}"
        assert runner.commands == [["mkdir", "-p", "/dst/snapshots"], ["mkdir", path]]

    def test_remote(self) -> None:
        sync, config, re = _remote_config()
        runner = FakeCommandRunner()

        path = create_snapshot_dir(
            sync,
            config,
            now=_NOW,
            platform="linux",
            resolved_endpoints=re,
            run=runner,
        )

        assert path == f"/backup/snapshots/{_TS_REMOTE.name}"
        assert runner.commands == [
            ["mkdir", "-p", "/backup/snapshots"],
            ["mkdir", path],
        ]
        assert {c.volume.slug for c in runner.calls} == {"dst"}

    def test_failure_raises(self) -> None:
        sync, config = _local_config()
        runner = FakeCommandRunner().on(
            "mkdir",
            returns=completed(1, stderr="permission denied"),
        )

        with pytest.raises(
            SnapshotOperationError,
            check=lambda e: (
                e.op is SnapshotOp.MKDIR and e.stderr == "permission denied"
            ),
        ):
            create_snapshot_dir(sync, config, now=_NOW, platform="linux", run=runner)


class TestCleanupOrphanedSnapshots:
    def test_deletes_orphans(self, tmp_path: Path) -> None:
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
        (snaps / "2024-01-01T00:00:00.000Z").mkdir()
        (snaps / "2024-01-02T00:00:00.000Z").mkdir()  # orphan
        latest = tmp_path / "latest"
        latest.symlink_to("snapshots/2024-01-01T00:00:00.000Z")

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert len(deleted) == 1
        assert "2024-01-02T00:00:00.000Z" in deleted[0]
        assert not (snaps / "2024-01-02T00:00:00.000Z").exists()

    def test_no_orphans(self, tmp_path: Path) -> None:
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

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert deleted == []

    @staticmethod
    def _leftover_config(tmp_path: Path) -> tuple[SyncConfig, Config]:
        dst = LocalVolume(slug="dst", path=str(tmp_path))
        src = LocalVolume(slug="src", path="/src")
        sync = SyncConfig(slug="s1", source="ep-src", destination="ep-dst")
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
        (tmp_path / "snapshots" / "2024-01-01T00:00:00.000Z").mkdir(parents=True)
        return sync, config

    def test_deletes_a_first_run_leftover_when_latest_is_dev_null(
        self,
        tmp_path: Path,
    ) -> None:
        """
        A first-run leftover is deleted while ``latest`` is ``/dev/null``.

        A first run interrupted before ``latest`` moved leaves a partial snapshot that
        would otherwise pass for a complete one later on.
        """
        sync, config = self._leftover_config(tmp_path)
        (tmp_path / "latest").symlink_to("/dev/null")

        deleted = cleanup_orphaned_snapshots(sync, config)

        assert deleted == [f"{tmp_path}/snapshots/2024-01-01T00:00:00.000Z"]
        assert list((tmp_path / "snapshots").iterdir()) == []

    def test_keeps_every_snapshot_when_latest_is_absent(self, tmp_path: Path) -> None:
        """
        An absent ``latest`` is outside damage, not "no sync yet": nothing is deleted.

        nbkp never removes the link, so the snapshots may be complete.
        """
        sync, config = self._leftover_config(tmp_path)

        with pytest.raises(SnapshotOperationError) as info:
            cleanup_orphaned_snapshots(sync, config)

        assert info.value.op == SnapshotOp.READ_LATEST
        assert info.value.path == f"{tmp_path}/latest"
        assert (tmp_path / "snapshots" / "2024-01-01T00:00:00.000Z").is_dir()

    def test_nothing_to_delete_without_snapshots_dir(self, tmp_path: Path) -> None:
        sync, config = self._leftover_config(tmp_path)
        (tmp_path / "snapshots" / "2024-01-01T00:00:00.000Z").rmdir()
        (tmp_path / "snapshots").rmdir()
        (tmp_path / "latest").symlink_to("/dev/null")

        assert cleanup_orphaned_snapshots(sync, config) == []


class TestDeleteSnapshot:
    def test_local(self, tmp_path: Path) -> None:
        snap = tmp_path / "snap1"
        snap.mkdir()
        (snap / "file.txt").write_text("data")
        vol = LocalVolume(slug="dst", path=str(tmp_path))

        delete_snapshot(str(snap), vol, {})
        assert not snap.exists()

    def test_remote(self) -> None:
        vol = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup")
        runner = FakeCommandRunner()

        delete_snapshot("/backup/snapshots/T1", vol, {}, run=runner)

        assert runner.commands == [["rm", "-rf", "/backup/snapshots/T1"]]


_S1 = "2024-01-01T00:00:00.000Z"
_S2 = "2024-01-02T00:00:00.000Z"
_S3 = "2024-01-03T00:00:00.000Z"


class TestPruneSnapshots:
    def test_prune_excess(self, tmp_path: Path) -> None:
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
                        enabled=True,
                        max_snapshots=2,
                    ),
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

        deleted = prune_snapshots(sync, config, 2)
        assert len(deleted) == 1
        assert _S1 in deleted[0]

    def test_never_prunes_latest(self, tmp_path: Path) -> None:
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
                        enabled=True,
                        max_snapshots=1,
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

        # max_snapshots=1, but _S1 is latest so only _S2 pruned
        deleted = prune_snapshots(sync, config, 1)
        assert len(deleted) == 1
        assert _S2 in deleted[0]
        assert (snaps / _S1).exists()

    def test_dry_run(self, tmp_path: Path) -> None:
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

        deleted = prune_snapshots(sync, config, 1, dry_run=True)
        assert len(deleted) == 2
        assert (snaps / _S1).exists()
        assert (snaps / _S2).exists()

    def test_no_excess(self, tmp_path: Path) -> None:
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
        (tmp_path / "snapshots" / _S1).mkdir(parents=True)

        deleted = prune_snapshots(sync, config, 5)
        assert deleted == []
