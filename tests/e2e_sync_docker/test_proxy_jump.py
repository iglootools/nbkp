"""Integration tests: proxy jump (bastion) support."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.rsync import run_rsync
from nbkp.remote.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.sync.testkit.seed import create_seed_sentinels

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec


class TestProxyJump:
    def test_sync_through_bastion(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
        proxied_ssh_endpoint: SshEndpoint,
    ) -> None:
        # Create local source files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "hello.txt").write_text("via bastion")

        src_vol = LocalVolume(slug="src", path=str(src_dir))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="proxied-server",
            path=REMOTE_BACKUP_PATH,
        )
        sync = SyncConfig(
            slug="test-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={
                "bastion": bastion_container,
                "proxied-server": proxied_ssh_endpoint,
            },
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
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

        # Verify file arrived via direct connection
        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/hello.txt",
        )
        assert check.returncode == 0
        assert check.stdout.strip() == "via bastion"

        assert_sentinels_after_sync(sync, config, docker_ssh_endpoint)
