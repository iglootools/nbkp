"""Integration test: end-to-end chain sync via generated script.

Same 6-hop chain as test_chain.py, but executed through a
generated bash script (`nbkp sh`) instead of `run_all_syncs`.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from nbkp.config import (
    SshEndpoint,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.orchestration import managed_mount
from nbkp.sh import ScriptOptions, generate_script

from nbkp.sync.testkit.seed import build_chain_config

from tests._docker_fixtures import LUKS_PASSPHRASE
from tests.e2e_docker._pipeline_helpers import (
    assert_chain_results,
    setup_chain,
)


class TestChainSyncSh:
    def test_generated_script_propagates_through_chain(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
        proxied_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """Generated script propagates data through the
        full 6-hop chain, same as the Python runner.

        Mount lifecycle is handled externally via managed_mount
        (matching the real-world usage where mount management is
        excluded from generated scripts).
        """
        # 1. Build config — with LUKS-encrypted btrfs volume
        config = build_chain_config(
            tmp_path,
            bastion_container,
            proxied_ssh_endpoint,
            luks_uuid=luks_uuid,
        )
        resolved = resolve_all_endpoints(config)

        # 2. Mount encrypted volume, then run the generated script
        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE):
            # 3. Setup: btrfs subvolume, sentinels, seed data
            src = setup_chain(config, tmp_path, docker_ssh_endpoint)

            # 4. Generate script
            script = generate_script(
                config,
                ScriptOptions(config_path="test.yaml"),
                resolved_endpoints=resolved,
            )

            # 5. Write script to file (piping via stdin would let
            #    SSH commands consume the remaining script text)
            script_path = tmp_path / "backup.sh"
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

            # 6. Validate syntax
            syntax = subprocess.run(
                ["bash", "-n", str(script_path)],
                capture_output=True,
                text=True,
            )
            assert syntax.returncode == 0, f"bash -n failed:\n{syntax.stderr}"

            # 7. Run the generated script
            result = subprocess.run(
                [str(script_path)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"script failed:\n{result.stderr}"
            assert "All syncs completed" in result.stderr

            # 8. Verify results (tree equality, snapshots, sentinels)
            assert_chain_results(src, tmp_path, config, docker_ssh_endpoint, resolved)
