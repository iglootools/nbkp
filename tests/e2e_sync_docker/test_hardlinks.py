"""Integration tests: hard-link snapshots via remote Docker container."""

from __future__ import annotations

import time
from pathlib import Path

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.snapshots.common import (
    list_snapshots,
    read_latest_symlink,
    update_latest_symlink,
)
from nbkp.sync.snapshots.hardlinks import (
    cleanup_orphaned_snapshots,
    create_snapshot_dir,
    prune_snapshots,
)
from nbkp.sync.rsync import run_rsync
from nbkp.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.testkit.gen.fs import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


def _make_hl_config(
    src_path: str,
    remote_hl_volume: RemoteVolume,
    docker_ssh_endpoint: SshEndpoint,
    max_snapshots: int | None = 5,
) -> tuple[SyncConfig, Config, ResolvedEndpoints]:
    """Build hard-link config and create seed sentinels."""
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
            "dst": remote_hl_volume,
        },
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

    def _run_remote(cmd: str) -> None:
        ssh_exec(docker_ssh_endpoint, cmd)

    create_seed_sentinels(config, remote_exec=_run_remote)

    resolved = resolve_all_endpoints(config)
    return sync, config, resolved


def _do_sync(
    src: Path,
    docker_ssh_endpoint: SshEndpoint,
    remote_hl_volume: RemoteVolume,
    max_snapshots: int | None = 5,
) -> tuple[SyncConfig, Config, ResolvedEndpoints, str]:
    """rsync + create snapshot dir + update symlink. Returns config
    tuple and the snapshot name."""
    sync, config, resolved = _make_hl_config(
        str(src), remote_hl_volume, docker_ssh_endpoint, max_snapshots
    )
    snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)
    snapshot_name = snapshot_path.rsplit("/", 1)[-1]

    result = run_rsync(
        sync,
        config,
        dest_suffix=f"snapshots/{snapshot_name}",
        resolved_endpoints=resolved,
    )
    assert result.returncode == 0

    update_latest_symlink(sync, config, snapshot_name, resolved_endpoints=resolved)

    assert_sentinels_after_sync(
        sync,
        config,
        docker_ssh_endpoint,
        dest_suffix=f"snapshots/{snapshot_name}",
    )

    return sync, config, resolved, snapshot_name


class TestHardLinkSnapshots:
    def test_snapshot_dir_created(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("snapshot me")

        sync, config, resolved = _make_hl_config(
            str(src), remote_hardlink_volume, docker_ssh_endpoint
        )
        snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)

        # Verify directory exists on remote
        check = ssh_exec(docker_ssh_endpoint, f"test -d {snapshot_path}")
        assert check.returncode == 0

    def test_rsync_into_snapshot(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "hello.txt").write_text("hello hard-link")

        sync, config, resolved = _make_hl_config(
            str(src), remote_hardlink_volume, docker_ssh_endpoint
        )
        snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)
        snapshot_name = snapshot_path.rsplit("/", 1)[-1]

        result = run_rsync(
            sync,
            config,
            dest_suffix=f"snapshots/{snapshot_name}",
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        # Verify file arrived in the snapshot dir
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {snapshot_path}/hello.txt",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "hello hard-link"

        assert_sentinels_after_sync(
            sync,
            config,
            docker_ssh_endpoint,
            dest_suffix=f"snapshots/{snapshot_name}",
        )

    def test_latest_symlink_updated(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("symlink test")

        sync, config, resolved, snap_name = _do_sync(
            src, docker_ssh_endpoint, remote_hardlink_volume
        )

        # Verify symlink exists and points to correct snapshot
        latest_name = read_latest_symlink(sync, config, resolved_endpoints=resolved)
        assert latest_name == snap_name

        # Verify the file is accessible via the symlink
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/latest/data.txt",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "symlink test"

    def test_second_sync_uses_link_dest(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("v1")

        # First sync
        sync, config, resolved, snap1 = _do_sync(
            src, docker_ssh_endpoint, remote_hardlink_volume
        )

        time.sleep(0.1)  # distinct timestamp

        # Second sync with link-dest from first snapshot
        snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)
        snap2 = snapshot_path.rsplit("/", 1)[-1]

        result = run_rsync(
            sync,
            config,
            link_dest=f"../{snap1}",
            dest_suffix=f"snapshots/{snap2}",
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        update_latest_symlink(sync, config, snap2, resolved_endpoints=resolved)

        # Verify second snapshot has the file
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/snapshots/{snap2}/file.txt",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "v1"

        # Verify latest now points to second snapshot
        latest_name = read_latest_symlink(sync, config, resolved_endpoints=resolved)
        assert latest_name == snap2

    def test_incremental_hard_links(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        """Unchanged files should be hard-linked between snapshots."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "unchanged.txt").write_text("same content")
        (src / "changed.txt").write_text("v1")

        # First sync
        sync, config, resolved, snap1 = _do_sync(
            src, docker_ssh_endpoint, remote_hardlink_volume
        )

        time.sleep(0.1)

        # Modify one file, leave the other unchanged
        (src / "changed.txt").write_text("v2 is different")

        # Second sync with --link-dest from first
        snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)
        snap2 = snapshot_path.rsplit("/", 1)[-1]
        result = run_rsync(
            sync,
            config,
            link_dest=f"../{snap1}",
            dest_suffix=f"snapshots/{snap2}",
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        # Verify the unchanged file shares inode (hard-linked)
        inode1 = ssh_exec(
            docker_ssh_endpoint,
            f"stat -c %i {REMOTE_BACKUP_PATH}/snapshots/{snap1}/unchanged.txt",
        )
        inode2 = ssh_exec(
            docker_ssh_endpoint,
            f"stat -c %i {REMOTE_BACKUP_PATH}/snapshots/{snap2}/unchanged.txt",
        )
        assert inode1.stdout.strip() == inode2.stdout.strip()

        # Verify the changed file has different content
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/snapshots/{snap2}/changed.txt",
        )
        assert check.stdout.strip() == "v2 is different"

    def test_dry_run_no_symlink_update(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("dry run")

        sync, config, resolved = _make_hl_config(
            str(src), remote_hardlink_volume, docker_ssh_endpoint
        )
        snapshot_path = create_snapshot_dir(sync, config, resolved_endpoints=resolved)
        snapshot_name = snapshot_path.rsplit("/", 1)[-1]

        # Dry-run rsync
        result = run_rsync(
            sync,
            config,
            dry_run=True,
            dest_suffix=f"snapshots/{snapshot_name}",
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        # Symlink should not exist (never updated)
        latest = read_latest_symlink(sync, config, resolved_endpoints=resolved)
        assert latest is None


class TestHardLinkOrphanCleanup:
    def test_orphan_cleaned_up(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("orphan test")

        # First sync (complete)
        sync, config, resolved, snap1 = _do_sync(
            src, docker_ssh_endpoint, remote_hardlink_volume
        )

        time.sleep(0.1)

        # Simulate a failed sync: create a snapshot dir newer than
        # latest but don't update the symlink
        ssh_exec(
            docker_ssh_endpoint,
            (f"mkdir -p {REMOTE_BACKUP_PATH}/snapshots/9999-99-99T00:00:00.000Z"),
        )

        # Verify orphan exists
        check = ssh_exec(
            docker_ssh_endpoint,
            (f"test -d {REMOTE_BACKUP_PATH}/snapshots/9999-99-99T00:00:00.000Z"),
        )
        assert check.returncode == 0

        # Cleanup should remove it
        deleted = cleanup_orphaned_snapshots(sync, config, resolved_endpoints=resolved)
        assert len(deleted) == 1
        assert "9999-99-99T00:00:00.000Z" in deleted[0]

        # Verify orphan is gone
        check = ssh_exec(
            docker_ssh_endpoint,
            (f"test -d {REMOTE_BACKUP_PATH}/snapshots/9999-99-99T00:00:00.000Z"),
            check=False,
        )
        assert check.returncode != 0

        # Verify the real snapshot is still there
        check = ssh_exec(
            docker_ssh_endpoint,
            f"test -d {REMOTE_BACKUP_PATH}/snapshots/{snap1}",
        )
        assert check.returncode == 0

    def test_no_cleanup_without_latest(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()

        sync, config, resolved = _make_hl_config(
            str(src), remote_hardlink_volume, docker_ssh_endpoint
        )

        # No latest symlink -> no cleanup possible
        deleted = cleanup_orphaned_snapshots(sync, config, resolved_endpoints=resolved)
        assert deleted == []


class TestHardLinkPrune:
    def _create_snapshots(
        self,
        src: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hl_volume: RemoteVolume,
        count: int,
        max_snapshots: int | None = None,
    ) -> tuple[SyncConfig, Config, ResolvedEndpoints, list[str]]:
        """Create multiple snapshots with distinct timestamps."""
        names: list[str] = []
        sync: SyncConfig | None = None
        config: Config | None = None
        resolved: ResolvedEndpoints | None = None

        for _ in range(count):
            sync, config, resolved, snap_name = _do_sync(
                src,
                docker_ssh_endpoint,
                remote_hl_volume,
                max_snapshots=max_snapshots,
            )
            names.append(snap_name)
            time.sleep(0.1)  # distinct timestamps

        assert sync is not None
        assert config is not None
        assert resolved is not None
        return sync, config, resolved, names

    def test_prune_deletes_oldest(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("prune test")

        sync, config, resolved, names = self._create_snapshots(
            src, docker_ssh_endpoint, remote_hardlink_volume, 3
        )

        # Prune to keep only 1
        deleted = prune_snapshots(sync, config, 1, resolved_endpoints=resolved)
        assert len(deleted) == 2

        # Verify only the latest snapshot remains
        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 1
        assert names[-1] in remaining[0]

    def test_prune_never_deletes_latest(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("never delete latest")

        sync, config, resolved, names = self._create_snapshots(
            src, docker_ssh_endpoint, remote_hardlink_volume, 2
        )

        # latest points to names[-1]; prune to 0 should still
        # keep it
        deleted = prune_snapshots(sync, config, 0, resolved_endpoints=resolved)

        # names[0] should be deleted, names[-1] (latest) kept
        assert len(deleted) == 1
        assert names[0] in deleted[0]

        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 1
        assert names[-1] in remaining[0]

    def test_prune_dry_run_keeps_all(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("dry run prune")

        sync, config, resolved, names = self._create_snapshots(
            src, docker_ssh_endpoint, remote_hardlink_volume, 3
        )

        # Dry-run prune to 1
        deleted = prune_snapshots(
            sync, config, 1, dry_run=True, resolved_endpoints=resolved
        )
        assert len(deleted) == 2

        # All 3 snapshots still exist
        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 3

    def test_prune_noop_under_limit(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_hardlink_volume: RemoteVolume,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.txt").write_text("noop prune")

        sync, config, resolved, _ = self._create_snapshots(
            src, docker_ssh_endpoint, remote_hardlink_volume, 2
        )

        # Prune with limit higher than count
        deleted = prune_snapshots(sync, config, 10, resolved_endpoints=resolved)
        assert deleted == []

        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 2
