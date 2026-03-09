"""Integration tests: local-to-local sync (no Docker needed)."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.sync.rsync import run_rsync


def _make_local_config(
    src_path: str,
    dst_path: str,
    src_subdir: str | None = None,
    dst_subdir: str | None = None,
    btrfs_snapshots: BtrfsSnapshotConfig | None = None,
) -> tuple[SyncConfig, Config]:
    src_vol = LocalVolume(slug="src", path=src_path)
    dst_vol = LocalVolume(slug="dst", path=dst_path)
    ep_src = SyncEndpoint(
        slug="ep-src",
        volume="src",
        subdir=src_subdir,
    )
    ep_dst_kwargs: dict[str, object] = {
        "slug": "ep-dst",
        "volume": "dst",
        "subdir": dst_subdir,
    }
    if btrfs_snapshots is not None:
        ep_dst_kwargs["btrfs_snapshots"] = btrfs_snapshots
    ep_dst = SyncEndpoint(**ep_dst_kwargs)  # type: ignore[arg-type]
    sync = SyncConfig(
        slug="test-sync",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        volumes={"src": src_vol, "dst": dst_vol},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"test-sync": sync},
    )
    return sync, config


class TestLocalToLocal:
    def test_basic_sync(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / "file1.txt").write_text("hello")
        (src / "file2.txt").write_text("world")

        sync, config = _make_local_config(str(src), str(dst))
        result = run_rsync(sync, config)

        assert result.returncode == 0
        assert (dst / "file1.txt").read_text() == "hello"
        assert (dst / "file2.txt").read_text() == "world"

    def test_incremental_sync(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / "file1.txt").write_text("version-one")

        sync, config = _make_local_config(str(src), str(dst))
        run_rsync(sync, config)
        assert (dst / "file1.txt").read_text() == "version-one"

        # Modify (different size) and re-sync
        (src / "file1.txt").write_text("version-two-updated")
        result = run_rsync(sync, config)

        assert result.returncode == 0
        assert (dst / "file1.txt").read_text() == "version-two-updated"

    def test_delete_propagation(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / "keep.txt").write_text("keep")
        (src / "remove.txt").write_text("remove")

        sync, config = _make_local_config(str(src), str(dst))
        run_rsync(sync, config)
        assert (dst / "remove.txt").exists()

        # Delete from source and re-sync (--delete is in rsync args)
        (src / "remove.txt").unlink()
        result = run_rsync(sync, config)

        assert result.returncode == 0
        assert (dst / "keep.txt").exists()
        assert not (dst / "remove.txt").exists()

    def test_dry_run_no_copy(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / "file.txt").write_text("data")

        sync, config = _make_local_config(str(src), str(dst))
        result = run_rsync(sync, config, dry_run=True)

        assert result.returncode == 0
        assert not (dst / "file.txt").exists()

    def test_filters_exclude_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / "keep.txt").write_text("keep")
        excluded = src / "excluded"
        excluded.mkdir()
        (excluded / "cache.tmp").write_text("should not sync")

        sync = SyncConfig(
            slug="test-sync",
            source="ep-src",
            destination="ep-dst",
            filters=["- excluded/"],
        )
        config = Config(
            volumes={
                "src": LocalVolume(slug="src", path=str(src)),
                "dst": LocalVolume(slug="dst", path=str(dst)),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"test-sync": sync},
        )
        result = run_rsync(sync, config)

        assert result.returncode == 0
        assert (dst / "keep.txt").read_text() == "keep"
        assert not (dst / "excluded").exists()

    def test_subdir(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "photos"
        dst = tmp_path / "dst" / "photos-backup"
        src.mkdir(parents=True)
        dst.mkdir(parents=True)

        (src / "img.jpg").write_text("jpeg-data")

        sync, config = _make_local_config(
            str(tmp_path / "src"),
            str(tmp_path / "dst"),
            src_subdir="photos",
            dst_subdir="photos-backup",
        )
        result = run_rsync(sync, config)

        assert result.returncode == 0
        assert (dst / "img.jpg").read_text() == "jpeg-data"
