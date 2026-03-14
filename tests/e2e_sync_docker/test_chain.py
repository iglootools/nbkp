"""Integration test: end-to-end chain sync pipeline.

Verifies data propagates through a 6-hop chain using all
supported sync variants and snapshot modes, with bastion SSH
for all remote access.  The btrfs snapshot volume lives on a
LUKS-encrypted filesystem to exercise encrypted volume I/O
end-to-end:

  src-local-bare -> stage-local-hl-snapshots ->
    stage-remote-bare -> stage-remote-btrfs-snapshots ->
    stage-remote-btrfs-bare -> stage-remote-hl-snapshots ->
    dst-local-bare

The Docker container runs sshd as PID 1 (not systemd), so
``stage-remote-btrfs-snapshots`` uses ``strategy="direct"`` and
the mount lifecycle is driven by the production
``mount_volumes`` / ``umount_volumes`` code path.
"""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    SshEndpoint,
    resolve_all_endpoints,
)
from nbkp.mount.detection import resolve_mount_strategy
from nbkp.mount.lifecycle import mount_volumes, umount_volumes
from nbkp.preflight import check_all_syncs
from nbkp.sync.runner import run_all_syncs

from tests._docker_fixtures import LUKS_PASSPHRASE
from tests.e2e_sync_docker._chain_helpers import (
    assert_chain_results,
    build_chain_config,
    setup_chain,
)


class TestChainSync:
    def test_data_propagates_through_chain(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
        proxied_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """Data seeded in src-local-bare arrives at
        dst-local-bare after traversing the full chain,
        with btrfs volumes on a LUKS-encrypted filesystem."""
        # 1. Build config — stage-remote-btrfs-snapshots has MountConfig
        config = build_chain_config(
            tmp_path,
            bastion_container,
            proxied_ssh_endpoint,
            luks_uuid=luks_uuid,
        )
        resolved = resolve_all_endpoints(config)

        # 2. Mount encrypted volume via production lifecycle
        mount_strategy = resolve_mount_strategy(config, resolved, names=None)
        mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            mount_strategy=mount_strategy,
        )
        assert all(r.success for r in mount_results), [
            r.detail for r in mount_results if not r.success
        ]

        try:
            # 3. Setup: sentinels, seed data
            src = setup_chain(config, tmp_path, docker_ssh_endpoint)

            # 4. Check all syncs — all should be active
            _, sync_statuses = check_all_syncs(config, resolved_endpoints=resolved)
            for slug, status in sync_statuses.items():
                assert status.active, f"{slug}: {[r.value for r in status.errors]}"

            # 5. Run all syncs (topologically ordered)
            results = run_all_syncs(
                config,
                sync_statuses,
                resolved_endpoints=resolved,
            )
            for r in results:
                assert r.success, f"{r.sync_slug}: {r.detail}"

            # 6. Verify topological ordering
            slugs = [r.sync_slug for r in results]
            for i in range(1, 6):
                assert slugs.index(f"step-{i}") < slugs.index(f"step-{i + 1}")

            # 7. Verify results (tree equality, snapshots, sentinels)
            assert_chain_results(src, tmp_path, config, docker_ssh_endpoint)
        finally:
            umount_volumes(
                config,
                resolved,
                mount_strategy=mount_strategy,
            )
