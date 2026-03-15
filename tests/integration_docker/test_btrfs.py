"""Integration tests: btrfs module via remote Docker container.

Includes both component-level tests (direct btrfs operations) and
sync-flow tests (rsync into staging → snapshot → verify).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from nbkp.fsprotocol import Snapshot
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.config.epresolution import ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.sync.rsync import run_rsync
from nbkp.sync.snapshots.btrfs import (
    create_snapshot,
    delete_snapshot,
    prune_snapshots,
)
from nbkp.sync.snapshots.common import (
    get_latest_snapshot,
    list_snapshots,
    update_latest_symlink,
)
from nbkp.remote.testkit.docker import REMOTE_BTRFS_PATH
from nbkp.sync.testkit.seed import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


def _make_btrfs_config(
    src_path: str,
    remote_btrfs_volume: RemoteVolume,
    docker_ssh_endpoint: SshEndpoint,
) -> tuple[SyncConfig, Config, ResolvedEndpoints]:
    """Build btrfs config and prepare remote destination."""
    src_vol = LocalVolume(slug="src", path=src_path)
    config = Config(
        ssh_endpoints={"test-server": docker_ssh_endpoint},
        volumes={"src": src_vol, "dst": remote_btrfs_volume},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={
            "test-sync": SyncConfig(
                slug="test-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )
    sync = config.syncs["test-sync"]

    resolved = resolve_all_endpoints(config)
    return sync, config, resolved


def _create_staging_subvolume(
    docker_ssh_endpoint: SshEndpoint,
) -> None:
    """Create the staging btrfs subvolume on the remote server."""
    ssh_exec(
        docker_ssh_endpoint,
        f"btrfs subvolume create {REMOTE_BTRFS_PATH}/staging",
    )
    ssh_exec(
        docker_ssh_endpoint,
        f"mkdir -p {REMOTE_BTRFS_PATH}/snapshots",
    )


def _seed_staging(
    docker_ssh_endpoint: SshEndpoint,
    content: str = "test data",
) -> None:
    """Put some data in the staging subvolume."""
    ssh_exec(
        docker_ssh_endpoint,
        f"echo '{content}' > {REMOTE_BTRFS_PATH}/staging/data.txt",
    )


class TestCreateSnapshot:
    def test_creates_readonly_snapshot(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        snapshot_path = create_snapshot(sync, config, resolved_endpoints=resolved)

        # Verify snapshot exists
        check = ssh_exec(docker_ssh_endpoint, f"test -d {snapshot_path}")
        assert check.returncode == 0

        # Verify it's readonly
        ro = ssh_exec(
            docker_ssh_endpoint,
            f"btrfs property get {snapshot_path} ro",
        )
        assert "ro=true" in ro.stdout


class TestListSnapshots:
    def test_lists_sorted_oldest_first(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

        create_snapshot(sync, config, now=now1, resolved_endpoints=resolved)
        create_snapshot(sync, config, now=now2, resolved_endpoints=resolved)

        snapshots = list_snapshots(sync, config, resolved)
        assert len(snapshots) == 2
        # Oldest first
        assert "2024-01-01" in snapshots[0].name
        assert "2024-01-02" in snapshots[1].name


class TestGetLatestSnapshot:
    def test_returns_most_recent(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

        create_snapshot(sync, config, now=now1, resolved_endpoints=resolved)
        create_snapshot(sync, config, now=now2, resolved_endpoints=resolved)

        latest = get_latest_snapshot(sync, config, resolved)
        assert latest is not None
        assert "2024-01-02" in latest.name

    def test_returns_none_when_empty(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        latest = get_latest_snapshot(sync, config, resolved)
        assert latest is None


class TestDeleteSnapshot:
    def test_deletes_subvolume(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        snapshot_path = create_snapshot(sync, config, resolved_endpoints=resolved)

        # Delete it
        delete_snapshot(snapshot_path, remote_btrfs_volume, resolved)

        # Verify it's gone
        check = ssh_exec(
            docker_ssh_endpoint,
            f"test -d {snapshot_path}",
            check=False,
        )
        assert check.returncode != 0


class TestPruneSnapshots:
    def test_prunes_oldest_beyond_limit(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        # Create 3 snapshots
        names = []
        for i in range(3):
            now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
            path = create_snapshot(
                sync,
                config,
                now=now,
                resolved_endpoints=resolved,
            )
            snapshot = Snapshot.from_path(path)
            names.append(snapshot.name)

        # Point latest to the newest
        update_latest_symlink(
            sync, config, Snapshot.from_name(names[-1]), resolved_endpoints=resolved
        )

        # Prune to keep 1
        deleted = prune_snapshots(sync, config, 1, resolved_endpoints=resolved)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 1
        assert names[-1] == remaining[0].name

    def test_dry_run_preserves_all(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        sync, config, resolved = _make_btrfs_config(
            str(tmp_path), remote_btrfs_volume, docker_ssh_endpoint
        )
        _create_staging_subvolume(docker_ssh_endpoint)
        _seed_staging(docker_ssh_endpoint)

        for i in range(3):
            now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
            path = create_snapshot(
                sync,
                config,
                now=now,
                resolved_endpoints=resolved,
            )
        snapshot = Snapshot.from_path(path)
        update_latest_symlink(sync, config, snapshot, resolved_endpoints=resolved)

        deleted = prune_snapshots(
            sync,
            config,
            1,
            dry_run=True,
            resolved_endpoints=resolved,
        )
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 3


# ── Sync-flow tests (rsync into staging → snapshot → verify) ────────


def _make_btrfs_sync_config(
    src_path: str,
    remote_btrfs_volume: RemoteVolume,
    docker_ssh_endpoint: SshEndpoint,
) -> tuple[SyncConfig, Config, ResolvedEndpoints]:
    """Build btrfs config with seed sentinels for sync-flow tests."""
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

        sync, config, resolved = _make_btrfs_sync_config(
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

        sync, config, resolved = _make_btrfs_sync_config(
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

        sync, config, resolved = _make_btrfs_sync_config(
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

        sync, config, resolved = _make_btrfs_sync_config(
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


class TestPruneBtrfsSnapshotsViaSyncFlow:
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

        sync, config, resolved = _make_btrfs_sync_config(
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

        sync, config, resolved = _make_btrfs_sync_config(
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

        sync, config, resolved = _make_btrfs_sync_config(
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
