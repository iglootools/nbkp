"""Integration test: end-to-end chain sync via generated script.

Same 6-hop chain as test_chain.py, but executed through a
generated bash script (`nbkp sh`) instead of `run_all_syncs`.
"""

import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from nbkp.config import (
    SshEndpoint,
)
from nbkp.disks.context import managed_mount
from nbkp.remote.resolution import resolve_all_endpoints
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
        config = build_chain_config(
            tmp_path,
            bastion_container,
            proxied_ssh_endpoint,
            luks_uuid=luks_uuid,
        )
        resolved = resolve_all_endpoints(config)

        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE):
            src = setup_chain(config, tmp_path, docker_ssh_endpoint, resolved)

            script = generate_script(
                config,
                ScriptOptions(platform=sys.platform, config_path="test.yaml"),
                now=datetime.now(UTC),
                resolved_endpoints=resolved,
            )

            # Run from a file: piped via stdin, the SSH commands in the script
            # would consume the rest of the script text.
            script_path = tmp_path / "backup.sh"
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

            syntax = subprocess.run(
                ["bash", "-n", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert syntax.returncode == 0, f"bash -n failed:\n{syntax.stderr}"

            result = subprocess.run(
                [str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, f"script failed:\n{result.stderr}"
            assert "All syncs completed" in result.stderr

            assert_chain_results(src, tmp_path, config, docker_ssh_endpoint, resolved)
