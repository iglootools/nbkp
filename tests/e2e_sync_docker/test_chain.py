"""Integration test: end-to-end chain sync pipeline.

Verifies data propagates through a 6-hop chain using all
supported sync variants and snapshot modes, with bastion SSH
for all remote access:

  src-local-bare → stage-local-hl-snapshots →
    stage-remote-bare → stage-remote-btrfs-snapshots →
    stage-remote-btrfs-bare → stage-remote-hl-snapshots →
    dst-local-bare
"""

from __future__ import annotations

from pathlib import Path

from nbkp.preflight import check_all_syncs
from nbkp.config import (
    SshEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.runner import run_all_syncs

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
    ) -> None:
        """Data seeded in src-local-bare arrives at
        dst-local-bare after traversing the full chain."""
        # 1. Build config
        config = build_chain_config(tmp_path, bastion_container, proxied_ssh_endpoint)

        # 2-4. Setup: btrfs subvolume, sentinels, seed data
        src = setup_chain(config, tmp_path, docker_ssh_endpoint)

        # 5. Check all syncs — all should be active
        resolved = resolve_all_endpoints(config)
        _, sync_statuses = check_all_syncs(config, resolved_endpoints=resolved)
        for slug, status in sync_statuses.items():
            assert status.active, f"{slug}: {[r.value for r in status.reasons]}"

        # 6. Run all syncs (topologically ordered)
        results = run_all_syncs(
            config,
            sync_statuses,
            resolved_endpoints=resolved,
        )
        for r in results:
            assert r.success, f"{r.sync_slug}: {r.detail}"

        # 7. Verify topological ordering
        slugs = [r.sync_slug for r in results]
        for i in range(1, 6):
            assert slugs.index(f"step-{i}") < slugs.index(f"step-{i + 1}")

        # 8. Verify results (tree equality, snapshots, sentinels)
        assert_chain_results(src, tmp_path, config, docker_ssh_endpoint)
