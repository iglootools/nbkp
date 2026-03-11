"""Component integration tests: btrfs module via remote Docker container."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
from nbkp.testkit.docker import REMOTE_BTRFS_PATH

from tests._docker_fixtures import ssh_exec


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
        assert "2024-01-01" in snapshots[0]
        assert "2024-01-02" in snapshots[1]


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
        assert "2024-01-02" in latest

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
            name = path.rsplit("/", 1)[-1]
            names.append(name)

        # Point latest to the newest
        update_latest_symlink(sync, config, names[-1], resolved_endpoints=resolved)

        # Prune to keep 1
        deleted = prune_snapshots(sync, config, 1, resolved_endpoints=resolved)
        assert len(deleted) == 2

        remaining = list_snapshots(sync, config, resolved)
        assert len(remaining) == 1
        assert names[-1] in remaining[0]

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
        name = path.rsplit("/", 1)[-1]
        update_latest_symlink(sync, config, name, resolved_endpoints=resolved)

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
