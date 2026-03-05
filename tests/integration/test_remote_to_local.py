"""Integration tests: remote-to-local sync (Docker)."""

from __future__ import annotations

from pathlib import Path

import pytest

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

from .conftest import assert_sentinels_after_sync, ssh_exec

pytestmark = pytest.mark.integration


class TestRemoteToLocal:
    def test_sync_from_container(
        self,
        tmp_path: Path,
        ssh_endpoint: SshEndpoint,
        remote_volume: RemoteVolume,
    ) -> None:
        # Create test file on remote source
        ssh_exec(
            ssh_endpoint,
            "echo 'hello from remote'"
            f" > {REMOTE_BACKUP_PATH}/remote-file.txt",
        )

        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()

        dst_vol = LocalVolume(slug="dst", path=str(dst_dir))
        sync = SyncConfig(
            slug="test-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
        )
        config = Config(
            ssh_endpoints={"test-server": ssh_endpoint},
            volumes={"src": remote_volume, "dst": dst_vol},
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        # Verify file arrived locally
        local_file = dst_dir / "remote-file.txt"
        assert local_file.exists()
        assert local_file.read_text().strip() == "hello from remote"

        assert_sentinels_after_sync(sync, config, ssh_endpoint)

    def test_sync_with_subdir(
        self,
        tmp_path: Path,
        ssh_endpoint: SshEndpoint,
        remote_volume: RemoteVolume,
    ) -> None:
        # Create test file in a subdir on remote source
        ssh_exec(
            ssh_endpoint,
            f"mkdir -p {REMOTE_BACKUP_PATH}/photos",
        )
        ssh_exec(
            ssh_endpoint,
            "echo 'image-data'" f" > {REMOTE_BACKUP_PATH}/photos/img.jpg",
        )

        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()

        dst_vol = LocalVolume(slug="dst", path=str(dst_dir))
        sync = SyncConfig(
            slug="test-sync",
            source=SyncEndpoint(volume="src", subdir="photos"),
            destination=DestinationSyncEndpoint(
                volume="dst", subdir="photos-backup"
            ),
        )
        config = Config(
            ssh_endpoints={"test-server": ssh_endpoint},
            volumes={"src": remote_volume, "dst": dst_vol},
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        local_file = dst_dir / "photos-backup" / "img.jpg"
        assert local_file.exists()
        assert local_file.read_text().strip() == "image-data"

        assert_sentinels_after_sync(sync, config, ssh_endpoint)
