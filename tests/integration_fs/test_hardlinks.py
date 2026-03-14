"""Integration tests: hard-link snapshots on local filesystem."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.sync.snapshots.common import (
    create_snapshot_timestamp,
    list_snapshots,
    update_latest_symlink,
)
from nbkp.sync.snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    delete_snapshot,
    prune_snapshots,
)
from nbkp.sync.rsync import run_rsync


def _make_config(
    src_path: str,
    dst_path: str,
    max_snapshots: int | None = 5,
) -> tuple[SyncConfig, Config]:
    """Build local hard-link config with sentinels."""
    src_vol = LocalVolume(slug="src", path=src_path)
    dst_vol = LocalVolume(slug="dst", path=dst_path)
    sync = SyncConfig(
        slug="test-sync",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        volumes={"src": src_vol, "dst": dst_vol},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=max_snapshots
                ),
            ),
        },
        syncs={"test-sync": sync},
    )

    # Create sentinels
    Path(src_path, ".nbkp-vol").touch()
    Path(src_path, ".nbkp-src").touch()
    Path(dst_path, ".nbkp-vol").touch()
    Path(dst_path, ".nbkp-dst").touch()

    return sync, config


def _do_sync(
    src: Path,
    dst: Path,
    max_snapshots: int | None = 5,
    now: datetime | None = None,
) -> tuple[SyncConfig, Config, str]:
    """Create snapshot dir + rsync + update symlink.

    Returns (sync, config, snapshot_name).
    """
    sync, config = _make_config(str(src), str(dst), max_snapshots)
    snapshot_path = create_snapshot_dir(sync, config, now=now)
    snapshot = Snapshot.from_path(snapshot_path)

    result = run_rsync(
        sync,
        config,
        dest_suffix=f"snapshots/{snapshot.name}",
    )
    assert result.returncode == 0

    update_latest_symlink(sync, config, snapshot)
    return sync, config, snapshot.name


class TestCreateSnapshotDir:
    def test_creates_timestamped_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("hello")

        sync, config = _make_config(str(src), str(dst))
        snapshot_path = create_snapshot_dir(sync, config)

        assert Path(snapshot_path).is_dir()
        assert "/snapshots/" in snapshot_path

    def test_creates_snapshots_parent(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        sync, config = _make_config(str(src), str(dst))
        snapshot_path = create_snapshot_dir(sync, config)

        snapshots_dir = Path(snapshot_path).parent
        assert snapshots_dir.name == "snapshots"
        assert snapshots_dir.is_dir()


class TestCleanupOrphanedSnapshots:
    def test_removes_orphaned_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("orphan test")

        sync, config, snap_name = _do_sync(src, dst)

        # Create an orphaned snapshot (newer than latest)
        dst_vol = config.volumes["dst"]
        orphan_snapshot = create_snapshot_timestamp(
            datetime(9999, 1, 1, tzinfo=timezone.utc), dst_vol
        )
        orphan = Path(str(dst)) / "snapshots" / orphan_snapshot.name
        orphan.mkdir(parents=True)

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert len(deleted) == 1
        assert orphan_snapshot.name in deleted[0]
        assert not orphan.exists()

    def test_preserves_latest_target(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("keep latest")

        sync, config, snap_name = _do_sync(src, dst)

        # Verify latest target still exists after cleanup
        cleanup_orphaned_snapshots(sync, config)
        snap_dir = Path(str(dst)) / "snapshots" / snap_name
        assert snap_dir.exists()

    def test_noop_when_no_latest(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        sync, config = _make_config(str(src), str(dst))

        deleted = cleanup_orphaned_snapshots(sync, config)
        assert deleted == []


class TestDeleteSnapshot:
    def test_deletes_snapshot_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        dst_vol = LocalVolume(slug="dst", path=str(dst))
        snap = create_snapshot_timestamp(
            datetime(2024, 1, 1, tzinfo=timezone.utc), dst_vol
        )
        snap_dir = dst / "snapshots" / snap.name
        snap_dir.mkdir(parents=True)
        (snap_dir / "file.txt").write_text("delete me")

        delete_snapshot(str(snap_dir), dst_vol, {})
        assert not snap_dir.exists()


class TestPruneSnapshots:
    def _create_snapshots(
        self,
        src: Path,
        dst: Path,
        count: int,
        max_snapshots: int | None = None,
    ) -> tuple[SyncConfig, Config, list[str]]:
        """Create multiple snapshots with distinct timestamps."""
        names: list[str] = []
        sync: SyncConfig | None = None
        config: Config | None = None

        for i in range(count):
            now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
            sync, config, snap_name = _do_sync(
                src, dst, max_snapshots=max_snapshots, now=now
            )
            names.append(snap_name)

        assert sync is not None
        assert config is not None
        return sync, config, names

    def test_prunes_oldest_beyond_limit(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("prune test")

        sync, config, names = self._create_snapshots(src, dst, 5, max_snapshots=3)

        deleted = prune_snapshots(sync, config, 3)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config)
        assert len(remaining) == 3
        # Oldest two should be deleted
        for snap in remaining:
            assert snap.name not in [names[0], names[1]]

    def test_never_deletes_latest_target(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("keep latest")

        sync, config, names = self._create_snapshots(src, dst, 3)

        # Prune to 0 — latest should still be kept
        deleted = prune_snapshots(sync, config, 0)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config)
        assert len(remaining) == 1
        assert names[-1] == remaining[0].name

    def test_dry_run_returns_paths_without_deleting(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("dry run prune")

        sync, config, names = self._create_snapshots(src, dst, 4)

        deleted = prune_snapshots(sync, config, 2, dry_run=True)
        assert len(deleted) == 2

        # All 4 should still exist
        remaining = list_snapshots(sync, config)
        assert len(remaining) == 4

    def test_noop_under_limit(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data.txt").write_text("noop")

        sync, config, names = self._create_snapshots(src, dst, 2)

        deleted = prune_snapshots(sync, config, 10)
        assert deleted == []

        remaining = list_snapshots(sync, config)
        assert len(remaining) == 2


class TestHardLinkIncremental:
    def test_unchanged_files_share_inodes(self, tmp_path: Path) -> None:
        """Unchanged files should be hard-linked between snapshots."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "unchanged.txt").write_text("same content")
        (src / "changed.txt").write_text("v1")
        (src / ".nbkp-vol").touch()
        (src / ".nbkp-src").touch()
        (dst / ".nbkp-vol").touch()
        (dst / ".nbkp-dst").touch()

        sync, config = _make_config(str(src), str(dst))

        # First sync
        now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        snap1_path = create_snapshot_dir(sync, config, now=now1)
        snap1 = Snapshot.from_path(snap1_path)
        result = run_rsync(
            sync,
            config,
            dest_suffix=f"snapshots/{snap1.name}",
        )
        assert result.returncode == 0
        update_latest_symlink(sync, config, snap1)

        # Change one file
        (src / "changed.txt").write_text("v2 is different")

        # Second sync with --link-dest
        now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
        snap2_path = create_snapshot_dir(sync, config, now=now2)
        snap2 = Snapshot.from_path(snap2_path)
        result = run_rsync(
            sync,
            config,
            link_dest=f"../{snap1.name}",
            dest_suffix=f"snapshots/{snap2.name}",
        )
        assert result.returncode == 0

        # Verify unchanged file shares inode (hard-linked)
        inode1 = os.stat(f"{snap1_path}/unchanged.txt").st_ino
        inode2 = os.stat(f"{snap2_path}/unchanged.txt").st_ino
        assert inode1 == inode2

        # Verify changed file has new content
        content = Path(f"{snap2_path}/changed.txt").read_text()
        assert content == "v2 is different"
