"""Integration tests: local-to-remote sync (Docker)."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    Config,
    DestinationSyncEndpoint,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.rsync import run_rsync
from nbkp.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.testkit.gen.fs import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


class TestLocalToRemote:
    def test_sync_to_container(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        # Create local source files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "hello.txt").write_text("hello from local")

        src_vol = LocalVolume(slug="src", path=str(src_dir))
        sync = SyncConfig(
            slug="test-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        # Verify file arrived on container
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/hello.txt",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "hello from local"

        assert_sentinels_after_sync(sync, config, docker_ssh_endpoint)

    def test_sync_with_subdir(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        # Create local source with subdir
        src_dir = tmp_path / "src" / "photos"
        src_dir.mkdir(parents=True)
        (src_dir / "img.jpg").write_text("image-data")

        src_vol = LocalVolume(slug="src", path=str(tmp_path / "src"))
        sync = SyncConfig(
            slug="test-sync",
            source=SyncEndpoint(volume="src", subdir="photos"),
            destination=DestinationSyncEndpoint(
                volume="dst", subdir="photos-backup"
            ),
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/photos-backup/img.jpg",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "image-data"

        assert_sentinels_after_sync(sync, config, docker_ssh_endpoint)
