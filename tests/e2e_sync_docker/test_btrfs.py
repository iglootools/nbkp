"""Integration tests: btrfs snapshots via remote Docker container."""

from __future__ import annotations

import time
from pathlib import Path

from nbkp.conventions import Snapshot
from nbkp.sync.snapshots.btrfs import (
    create_snapshot,
    prune_snapshots,
)
from nbkp.sync.snapshots.common import (
    get_latest_snapshot,
    list_snapshots,
    update_latest_symlink,
)
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.rsync import run_rsync
from nbkp.testkit.docker import REMOTE_BTRFS_PATH
from nbkp.testkit.gen.fs import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


def _make_btrfs_config(
    src_path: str,
    remote_btrfs_volume: RemoteVolume,
    docker_ssh_endpoint: SshEndpoint,
) -> tuple[SyncConfig, Config, ResolvedEndpoints]:
    """Build btrfs config and create seed sentinels."""
    src_vol = LocalVolume(slug="src", path=src_path)
    sync = SyncConfig(
        slug="test-sync",
        source="ep-src",
        destination="ep-dst",
    )
    config = Config(
        ssh_endpoints={"test-server": docker_ssh_endpoint},
        volumes={
            "src": src_vol,
            "dst": remote_btrfs_volume,
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"test-sync": sync},
    )

    def _run_remote(cmd: str) -> None:
        ssh_exec(docker_ssh_endpoint, cmd)

    create_seed_sentinels(config, remote_exec=_run_remote)

    resolved = resolve_all_endpoints(config)
    return sync, config, resolved


class TestBtrfsSnapshots:
    def test_snapshot_created(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("snapshot me")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )

        # Rsync into staging
        result = run_rsync(
            sync, config, resolved_endpoints=resolved, dest_suffix="staging"
        )
        assert result.returncode == 0

        # Create snapshot
        snapshot_path = create_snapshot(sync, config, resolved_endpoints=resolved)

        # Verify snapshot exists
        check = ssh_exec(docker_ssh_endpoint, f"test -d {snapshot_path}")
        assert check.returncode == 0

        # Update latest symlink
        snapshot = Snapshot.from_path(snapshot_path)
        update_latest_symlink(sync, config, snapshot, resolved_endpoints=resolved)

        # Verify latest symlink
        link = ssh_exec(
            docker_ssh_endpoint,
            f"readlink {REMOTE_BTRFS_PATH}/latest",
        )
        assert f"snapshots/{snapshot.name}" in link.stdout

        assert_sentinels_after_sync(
            sync, config, docker_ssh_endpoint, dest_suffix="staging"
        )

    def test_snapshot_readonly(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("readonly test")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )
        run_rsync(sync, config, resolved_endpoints=resolved, dest_suffix="staging")
        snapshot_path = create_snapshot(sync, config, resolved_endpoints=resolved)

        # Check readonly property
        check = ssh_exec(
            docker_ssh_endpoint,
            f"btrfs property get {snapshot_path} ro",
        )
        assert check.returncode == 0
        assert "ro=true" in check.stdout

    def test_second_sync_link_dest(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("v1")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )

        # First sync + snapshot + symlink
        run_rsync(sync, config, resolved_endpoints=resolved, dest_suffix="staging")
        first_snap_path = create_snapshot(sync, config, resolved_endpoints=resolved)
        first_snapshot = Snapshot.from_path(first_snap_path)
        update_latest_symlink(sync, config, first_snapshot, resolved_endpoints=resolved)

        # Small delay to ensure distinct timestamp
        time.sleep(0.1)

        # Second sync should use link-dest from first snapshot
        latest_snap = get_latest_snapshot(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert latest_snap is not None

        link_dest = f"../snapshots/{latest_snap.name}"
        result = run_rsync(
            sync,
            config,
            link_dest=link_dest,
            resolved_endpoints=resolved,
            dest_suffix="staging",
        )
        assert result.returncode == 0

        # Create second snapshot + symlink
        snapshot_path = create_snapshot(sync, config, resolved_endpoints=resolved)
        check = ssh_exec(docker_ssh_endpoint, f"test -d {snapshot_path}")
        assert check.returncode == 0

        snapshot = Snapshot.from_path(snapshot_path)
        update_latest_symlink(sync, config, snapshot, resolved_endpoints=resolved)

        # Verify latest symlink points to second snapshot
        link = ssh_exec(
            docker_ssh_endpoint,
            f"readlink {REMOTE_BTRFS_PATH}/latest",
        )
        assert f"snapshots/{snapshot.name}" in link.stdout

        assert_sentinels_after_sync(
            sync, config, docker_ssh_endpoint, dest_suffix="staging"
        )

    def test_dry_run_no_snapshot(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        # Count existing snapshots before dry run
        before = ssh_exec(
            docker_ssh_endpoint,
            f"ls {REMOTE_BTRFS_PATH}/snapshots 2>/dev/null || true",
        )
        count_before = len([s for s in before.stdout.strip().split("\n") if s.strip()])

        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("dry run")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )

        # Dry-run rsync
        result = run_rsync(
            sync,
            config,
            dry_run=True,
            resolved_endpoints=resolved,
            dest_suffix="staging",
        )
        assert result.returncode == 0

        # Verify no new snapshot was created
        after = ssh_exec(
            docker_ssh_endpoint,
            f"ls {REMOTE_BTRFS_PATH}/snapshots 2>/dev/null || true",
        )
        count_after = len([s for s in after.stdout.strip().split("\n") if s.strip()])
        assert count_after == count_before


class TestPruneSnapshots:
    def _create_snapshots(
        self,
        sync: SyncConfig,
        config: Config,
        resolved: ResolvedEndpoints,
        count: int,
    ) -> list[str]:
        """Create multiple snapshots with latest symlink updates."""
        paths: list[str] = []
        for _ in range(count):
            path = create_snapshot(
                sync,
                config,
                resolved_endpoints=resolved,
            )
            snapshot = Snapshot.from_path(path)
            update_latest_symlink(sync, config, snapshot, resolved_endpoints=resolved)
            paths.append(path)
            time.sleep(0.1)  # distinct timestamps
        return paths

    def test_prune_deletes_oldest_snapshots(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("prune test")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )
        run_rsync(sync, config, resolved_endpoints=resolved, dest_suffix="staging")

        self._create_snapshots(sync, config, resolved, 3)

        # Prune to keep only 1
        deleted = prune_snapshots(
            sync,
            config,
            max_snapshots=1,
            resolved_endpoints=resolved,
        )
        assert len(deleted) == 2

        # Verify only 1 snapshot remains
        remaining = list_snapshots(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert len(remaining) == 1

    def test_prune_dry_run_keeps_all(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("dry run prune")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )
        run_rsync(sync, config, resolved_endpoints=resolved, dest_suffix="staging")

        self._create_snapshots(sync, config, resolved, 3)

        # Dry-run prune
        deleted = prune_snapshots(
            sync,
            config,
            max_snapshots=1,
            dry_run=True,
            resolved_endpoints=resolved,
        )
        assert len(deleted) == 2

        # All 3 snapshots still exist
        remaining = list_snapshots(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert len(remaining) == 3

    def test_prune_noop_when_under_limit(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("noop prune")

        sync, config, resolved = _make_btrfs_config(
            str(src), remote_btrfs_volume, docker_ssh_endpoint
        )
        run_rsync(sync, config, resolved_endpoints=resolved, dest_suffix="staging")

        self._create_snapshots(sync, config, resolved, 2)

        # Prune with limit higher than count
        deleted = prune_snapshots(
            sync,
            config,
            max_snapshots=5,
            resolved_endpoints=resolved,
        )
        assert deleted == []

        # All 2 snapshots still exist
        remaining = list_snapshots(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert len(remaining) == 2
