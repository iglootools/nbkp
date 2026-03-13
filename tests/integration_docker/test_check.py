"""Integration tests: volume and sync checks."""

from __future__ import annotations

from pathlib import Path

from nbkp.preflight import (
    SyncError,
    check_sync,
    check_volume,
)
from nbkp.preflight.snapshot_checks import (
    _check_btrfs_filesystem,
    _check_btrfs_subvolume,
)
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.testkit.docker import REMOTE_BACKUP_PATH, REMOTE_BTRFS_PATH
from nbkp.testkit.gen.fs import create_seed_sentinels

from tests._docker_fixtures import create_sentinels, ssh_exec


class TestLocalVolumeCheck:
    def test_local_volume_active(self, tmp_path: Path) -> None:
        vol_path = tmp_path / "vol"
        vol_path.mkdir()
        (vol_path / ".nbkp-vol").touch()

        vol = LocalVolume(slug="local", path=str(vol_path))
        status = check_volume(vol)
        assert status.active is True

    def test_local_volume_inactive(self, tmp_path: Path) -> None:
        vol_path = tmp_path / "vol"
        vol_path.mkdir()
        # No .nbkp-vol sentinel

        vol = LocalVolume(slug="local", path=str(vol_path))
        status = check_volume(vol)
        assert status.active is False


class TestRemoteVolumeCheck:
    def test_remote_volume_active(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        create_sentinels(docker_ssh_endpoint, REMOTE_BACKUP_PATH, [".nbkp-vol"])
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        status = check_volume(docker_remote_volume, resolved)
        assert status.active is True

    def test_remote_volume_inactive(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        # No sentinel created
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        status = check_volume(docker_remote_volume, resolved)
        assert status.active is False


class TestSyncCheck:
    def test_sync_status_active(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        src_path = tmp_path / "src"
        src_vol = LocalVolume(slug="src", path=str(src_path))
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
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

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)

        status = check_sync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert status.active is True
        assert status.errors == []


class TestBtrfsFilesystemCheck:
    def test_btrfs_path_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert _check_btrfs_filesystem(remote_btrfs_volume, resolved) is True

    def test_non_btrfs_path_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"data": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert _check_btrfs_filesystem(docker_remote_volume, resolved) is False


class TestBtrfsSubvolumeCheck:
    def test_subvolume_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        ssh_exec(
            docker_ssh_endpoint,
            f"btrfs subvolume create {REMOTE_BTRFS_PATH}/test-subvol",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert (
            _check_btrfs_subvolume(
                remote_btrfs_volume,
                "test-subvol",
                resolved,
            )
            is True
        )

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"btrfs subvolume delete {REMOTE_BTRFS_PATH}/test-subvol",
        )

    def test_regular_dir_not_subvolume(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        ssh_exec(
            docker_ssh_endpoint,
            f"mkdir -p {REMOTE_BTRFS_PATH}/regular-dir",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert (
            _check_btrfs_subvolume(
                remote_btrfs_volume,
                "regular-dir",
                resolved,
            )
            is False
        )

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"rm -rf {REMOTE_BTRFS_PATH}/regular-dir",
        )


class TestSyncCheckBtrfs:
    def test_sync_inactive_when_not_subvolume(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        # Create a regular directory (not a subvolume)
        ssh_exec(
            docker_ssh_endpoint,
            f"mkdir -p {REMOTE_BTRFS_PATH}/not-a-subvol",
        )
        create_sentinels(
            docker_ssh_endpoint,
            REMOTE_BTRFS_PATH,
            [".nbkp-vol"],
        )
        create_sentinels(
            docker_ssh_endpoint,
            f"{REMOTE_BTRFS_PATH}/not-a-subvol",
            [".nbkp-dst"],
        )

        src_path = tmp_path / "src"
        src_path.mkdir()
        (src_path / ".nbkp-vol").touch()
        (src_path / ".nbkp-src").touch()

        src_vol = LocalVolume(slug="src", path=str(src_path))
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
                    subdir="not-a-subvol",
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

        status = check_sync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert status.active is False
        assert SyncError.DESTINATION_NOT_BTRFS_SUBVOLUME in status.errors

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"rm -rf {REMOTE_BTRFS_PATH}/not-a-subvol",
        )
