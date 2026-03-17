"""Integration tests: subdir-to-subdir endpoint mapping (Docker).

Basic rsync for all direction combinations is covered by
``e2e_docker/test_pipeline``.  These tests isolate subdir mapping
for local-to-remote and remote-to-local syncs.
"""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.sync.rsync import run_rsync
from nbkp.remote.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.sync.testkit.seed import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


class TestSubdirSync:
    def test_local_to_remote_with_subdir(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        src_dir = tmp_path / "src" / "photos"
        src_dir.mkdir(parents=True)
        (src_dir / "img.jpg").write_text("image-data")

        src_vol = LocalVolume(slug="src", path=str(tmp_path / "src"))
        sync = SyncConfig(
            slug="test-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    subdir="photos-backup",
                ),
            },
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(sync, config, resolved_endpoints=resolved)
        assert result.returncode == 0

        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/photos-backup/img.jpg",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "image-data"

        assert_sentinels_after_sync(sync, config, docker_ssh_endpoint)

    def test_remote_to_local_with_subdir(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        ssh_exec(
            docker_ssh_endpoint,
            f"mkdir -p {REMOTE_BACKUP_PATH}/photos",
        )
        ssh_exec(
            docker_ssh_endpoint,
            f"echo 'image-data' > {REMOTE_BACKUP_PATH}/photos/img.jpg",
        )

        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()

        dst_vol = LocalVolume(slug="dst", path=str(dst_dir))
        sync = SyncConfig(
            slug="test-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": docker_remote_volume, "dst": dst_vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    subdir="photos-backup",
                ),
            },
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(sync, config, resolved_endpoints=resolved)
        assert result.returncode == 0

        local_file = dst_dir / "photos-backup" / "img.jpg"
        assert local_file.exists()
        assert local_file.read_text().strip() == "image-data"

        assert_sentinels_after_sync(sync, config, docker_ssh_endpoint)
