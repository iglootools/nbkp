"""Component integration tests: btrfs module on local filesystem.

These tests exercise the local (subprocess) code path of the btrfs
module. They require Linux with btrfs-progs installed and a btrfs
mount point. Run via: mise run test-btrfs-local

Skipped automatically when btrfs is unavailable (e.g. on macOS).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    DestinationSyncEndpoint,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.sync.btrfs import (
    create_snapshot,
    delete_snapshot,
    get_latest_snapshot,
    list_snapshots,
    prune_snapshots,
)
from nbkp.sync.symlink import update_latest_symlink

_skip_no_btrfs = pytest.mark.skipif(
    platform.system() != "Linux" or shutil.which("btrfs") is None,
    reason="btrfs not available (requires Linux + btrfs-progs)",
)

pytestmark = _skip_no_btrfs

# The btrfs mount point — set by the Docker entrypoint or CI
BTRFS_MOUNT = os.environ.get("NBKP_BTRFS_MOUNT", "/srv/btrfs-backups")


def _make_btrfs_config(
    src_path: str,
    dst_path: str,
) -> tuple[SyncConfig, Config]:
    """Build local btrfs config."""
    src_vol = LocalVolume(slug="src", path=src_path)
    dst_vol = LocalVolume(slug="dst", path=dst_path)
    sync = SyncConfig(
        slug="test-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    )
    config = Config(
        volumes={"src": src_vol, "dst": dst_vol},
        syncs={"test-sync": sync},
    )
    return sync, config


@pytest.fixture()
def btrfs_dst(tmp_path: Path) -> Path:
    """Create a destination directory on the btrfs mount.

    Uses a subdirectory of BTRFS_MOUNT to ensure btrfs ops work.
    Falls back to tmp_path if BTRFS_MOUNT doesn't exist.
    """
    mount = Path(BTRFS_MOUNT)
    if mount.is_dir():
        dst = mount / f"test-{os.getpid()}"
        dst.mkdir(exist_ok=True)
        yield dst
        # Cleanup: delete any snapshot subvolumes first
        snaps_dir = dst / "snapshots"
        if snaps_dir.is_dir():
            for snap in sorted(snaps_dir.iterdir()):
                subprocess.run(
                    [
                        "btrfs",
                        "property",
                        "set",
                        str(snap),
                        "ro",
                        "false",
                    ],
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "btrfs",
                        "subvolume",
                        "delete",
                        str(snap),
                    ],
                    capture_output=True,
                )
        tmp_sub = dst / "tmp"
        if tmp_sub.is_dir():
            subprocess.run(
                [
                    "btrfs",
                    "subvolume",
                    "delete",
                    str(tmp_sub),
                ],
                capture_output=True,
            )
        shutil.rmtree(dst, ignore_errors=True)
    else:
        pytest.skip(f"btrfs mount not found: {BTRFS_MOUNT}")


def _create_tmp_subvolume(dst: Path) -> None:
    """Create the tmp btrfs subvolume locally."""
    subprocess.run(
        ["btrfs", "subvolume", "create", str(dst / "tmp")],
        check=True,
        capture_output=True,
    )
    (dst / "snapshots").mkdir(exist_ok=True)


def _seed_tmp(dst: Path, content: str = "test data") -> None:
    """Put data in the tmp subvolume."""
    (dst / "tmp" / "data.txt").write_text(content)


class TestCreateSnapshot:
    def test_creates_readonly_snapshot(
        self, tmp_path: Path, btrfs_dst: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        snapshot_path = create_snapshot(sync, config)

        assert Path(snapshot_path).is_dir()

        # Verify readonly
        result = subprocess.run(
            ["btrfs", "property", "get", snapshot_path, "ro"],
            capture_output=True,
            text=True,
        )
        assert "ro=true" in result.stdout


class TestListSnapshots:
    def test_lists_sorted_oldest_first(
        self, tmp_path: Path, btrfs_dst: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

        create_snapshot(sync, config, now=now1)
        create_snapshot(sync, config, now=now2)

        snapshots = list_snapshots(sync, config)
        assert len(snapshots) == 2
        assert "2024-01-01" in snapshots[0]
        assert "2024-01-02" in snapshots[1]


class TestGetLatestSnapshot:
    def test_returns_most_recent(
        self, tmp_path: Path, btrfs_dst: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

        create_snapshot(sync, config, now=now1)
        create_snapshot(sync, config, now=now2)

        latest = get_latest_snapshot(sync, config)
        assert latest is not None
        assert "2024-01-02" in latest


class TestDeleteSnapshot:
    def test_deletes_subvolume(self, tmp_path: Path, btrfs_dst: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        snapshot_path = create_snapshot(sync, config)
        assert Path(snapshot_path).is_dir()

        dst_vol = config.volumes["dst"]
        delete_snapshot(snapshot_path, dst_vol, {})

        assert not Path(snapshot_path).exists()


class TestPruneSnapshots:
    def test_prunes_oldest_beyond_limit(
        self, tmp_path: Path, btrfs_dst: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        names = []
        for i in range(3):
            now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
            path = create_snapshot(sync, config, now=now)
            names.append(path.rsplit("/", 1)[-1])

        update_latest_symlink(sync, config, names[-1])

        deleted = prune_snapshots(sync, config, 1)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config)
        assert len(remaining) == 1
        assert names[-1] in remaining[0]

    def test_dry_run_preserves_all(
        self, tmp_path: Path, btrfs_dst: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config = _make_btrfs_config(str(src), str(btrfs_dst))
        _create_tmp_subvolume(btrfs_dst)
        _seed_tmp(btrfs_dst)

        for i in range(3):
            now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
            path = create_snapshot(sync, config, now=now)
        name = path.rsplit("/", 1)[-1]
        update_latest_symlink(sync, config, name)

        deleted = prune_snapshots(sync, config, 1, dry_run=True)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config)
        assert len(remaining) == 3
