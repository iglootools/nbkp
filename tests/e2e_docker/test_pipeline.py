"""Integration test: end-to-end chain sync pipeline.

Verifies data propagates through a 6-hop chain using all
supported sync variants and snapshot modes, with bastion SSH
for all remote access.  The btrfs snapshot volume lives on a
LUKS-encrypted filesystem to exercise encrypted volume I/O
end-to-end.

Chain topology (6 hops)::

  step-1  local → local            (HL snapshots dest)
  step-2  local → remote           (bare dest, via bastion)
  step-3  remote → remote          (btrfs snapshots dest, same server)
  step-4  remote → remote          (bare dest on btrfs, same server)
  step-5  remote → remote          (HL snapshots dest, same server)
  step-6  remote → local           (bare dest, via bastion)

Covered scenarios:

- All four sync direction combinations (local→local, local→remote,
  remote→remote same-server, remote→local)
- Both snapshot modes (hard-link and btrfs) as source and destination
- Bastion/proxy-jump SSH for all remote access
- LUKS-encrypted btrfs volume via ``managed_mount`` lifecycle
- Rsync filter exclusion (``excluded/`` absent from all destinations)
- Topological ordering of dependent syncs
- Failure propagation (skipped upstream → cancelled downstream)
- Sentinel preservation across syncs
"""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    SshEndpoint,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.remote.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.disks.context import managed_mount
from nbkp.run.pipeline import Strictness, check_and_run
from nbkp.sync.runner import SyncOutcome

from nbkp.sync.testkit.seed import build_chain_config

from tests._docker_fixtures import LUKS_PASSPHRASE, ssh_exec
from tests.e2e_docker._pipeline_helpers import (
    assert_chain_results,
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
        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE) as (
            _mount_strategy,
            mount_observations,
        ):
            # 3. Setup: sentinels, seed data
            src = setup_chain(config, tmp_path, docker_ssh_endpoint)

            # 4–5. Preflight checks + run all syncs (production pipeline)
            pipeline = check_and_run(
                config,
                strictness=Strictness.IGNORE_NONE,
                resolved_endpoints=resolved,
                mount_observations=mount_observations,
            )
            assert not pipeline.has_preflight_errors, {
                slug: [e.value for e in s.errors]
                for slug, s in pipeline.sync_statuses.items()
                if s.errors
            }
            for r in pipeline.results:
                assert r.success, f"{r.sync_slug}: {r.detail}"

            # 6. Verify topological ordering
            slugs = [r.sync_slug for r in pipeline.results]
            for i in range(1, 6):
                assert slugs.index(f"step-{i}") < slugs.index(f"step-{i + 1}")

            # 7. Verify results (tree equality, snapshots, sentinels)
            assert_chain_results(src, tmp_path, config, docker_ssh_endpoint, resolved)

    def test_failure_cancels_downstream_syncs(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
        proxied_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """When an upstream sync is skipped (inactive), all transitive
        downstream syncs are cancelled via failure propagation."""
        # 1. Build the same 6-hop chain config
        config = build_chain_config(
            tmp_path,
            bastion_container,
            proxied_ssh_endpoint,
            luks_uuid=luks_uuid,
        )
        resolved = resolve_all_endpoints(config)

        # 2. Mount encrypted volume via production lifecycle
        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE) as (
            _mount_strategy,
            mount_observations,
        ):
            # 3. Setup: sentinels, seed data
            setup_chain(config, tmp_path, docker_ssh_endpoint)

            # 4. Deliberately remove the .nbkp-dst sentinel on step-2's
            #    destination (ep-stage-remote-bare) to make step-2 inactive
            ssh_exec(
                docker_ssh_endpoint,
                f"rm -f {REMOTE_BACKUP_PATH}/bare/.nbkp-dst",
            )

            # 5. Run pipeline in non-strict mode so missing sentinels
            #    cause skips rather than a hard preflight failure
            pipeline = check_and_run(
                config,
                strictness=Strictness.IGNORE_INACTIVE,
                resolved_endpoints=resolved,
                mount_observations=mount_observations,
            )

            # 6. Verify outcomes
            results_by_slug = {r.sync_slug: r for r in pipeline.results}

            # step-1 (local->local) has no dependency on step-2
            assert results_by_slug["step-1"].success is True

            # step-2 is skipped because its destination sentinel is missing
            assert results_by_slug["step-2"].outcome == SyncOutcome.SKIPPED

            # step-3 through step-6 are cancelled because they depend
            # transitively on step-2 through the chain
            assert results_by_slug["step-3"].outcome == SyncOutcome.CANCELLED
            assert results_by_slug["step-4"].outcome == SyncOutcome.CANCELLED
            assert results_by_slug["step-5"].outcome == SyncOutcome.CANCELLED
            assert results_by_slug["step-6"].outcome == SyncOutcome.CANCELLED
