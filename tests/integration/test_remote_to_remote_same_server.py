"""Integration tests: remote-to-remote sync, same server (Docker)."""

from __future__ import annotations

import pytest

from nbkp.config import (
    Config,
    DestinationSyncEndpoint,
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


class TestRemoteToRemoteSameServer:
    def test_sync_on_container(
        self,
        ssh_endpoint: SshEndpoint,
    ) -> None:
        src_vol = RemoteVolume(
            slug="src-remote",
            ssh_endpoint="test-server",
            path=f"{REMOTE_BACKUP_PATH}/src",
        )
        dst_vol = RemoteVolume(
            slug="dst-remote",
            ssh_endpoint="test-server",
            path=f"{REMOTE_BACKUP_PATH}/dst",
        )
        sync = SyncConfig(
            slug="test-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
        )
        config = Config(
            ssh_endpoints={"test-server": ssh_endpoint},
            volumes={"src": src_vol, "dst": dst_vol},
            syncs={"test-sync": sync},
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        # Create test file on remote source
        ssh_exec(
            ssh_endpoint,
            "echo 'hello from remote'"
            f" > {REMOTE_BACKUP_PATH}/src/remote-file.txt",
        )

        resolved = resolve_all_endpoints(config)
        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert result.returncode == 0

        out = ssh_exec(
            ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/dst/remote-file.txt",
        )
        assert out.stdout.strip() == "hello from remote"

        assert_sentinels_after_sync(sync, config, ssh_endpoint)
