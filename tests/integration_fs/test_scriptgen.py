"""Integration tests: script generation with chain configs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.sh import ScriptOptions, generate_script
from nbkp.sync.testkit.seed import (
    SEED_EXCLUDE_FILTERS,
    create_seed_sentinels,
    seed_volume,
)


def _has_bash4() -> bool:
    """Check whether a bash 4+ binary is available."""
    bash = shutil.which("bash")
    if bash is None:
        return False
    r = subprocess.run(
        [bash, "-c", "declare -A x=()"],
        capture_output=True,
    )
    return r.returncode == 0


_requires_bash4 = pytest.mark.skipif(
    not _has_bash4(),
    reason="bash 4+ not available",
)


def _build_chain_config(tmp_path: Path) -> Config:
    """Build a local-only 2-hop chain config.

    Mirrors the structure of the Docker chain test but
    restricted to local volumes (no SSH/remote).
    """
    hl = HardLinkSnapshotConfig(enabled=True)

    volumes: dict[str, LocalVolume | RemoteVolume] = {
        "src": LocalVolume(
            slug="src",
            path=str(tmp_path / "src"),
        ),
        "stage": LocalVolume(
            slug="stage",
            path=str(tmp_path / "stage"),
        ),
        "dst": LocalVolume(
            slug="dst",
            path=str(tmp_path / "dst"),
        ),
    }
    sync_endpoints: dict[str, SyncEndpoint] = {
        "ep-src": SyncEndpoint(
            slug="ep-src",
            volume="src",
        ),
        "ep-stage": SyncEndpoint(
            slug="ep-stage",
            volume="stage",
            hard_link_snapshots=hl,
        ),
        "ep-dst": SyncEndpoint(
            slug="ep-dst",
            volume="dst",
        ),
    }
    syncs: dict[str, SyncConfig] = {
        "step-1": SyncConfig(
            slug="step-1",
            source="ep-src",
            destination="ep-stage",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        "step-2": SyncConfig(
            slug="step-2",
            source="ep-stage",
            destination="ep-dst",
            filters=SEED_EXCLUDE_FILTERS,
        ),
    }
    return Config(
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )


class TestGeneratedScriptSyntax:
    @_requires_bash4
    def test_chain_config_valid_bash_no_portable(self, tmp_path: Path) -> None:
        """--no-portable script passes bash -n (bash 4+)."""
        config = _build_chain_config(tmp_path)
        create_seed_sentinels(config)
        seed_volume(config.volumes["src"])

        script = generate_script(
            config,
            ScriptOptions(config_path="test.yaml", portable=False),
        )

        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_chain_config_valid_bash32(self, tmp_path: Path) -> None:
        """Generated script passes syntax check with /bin/bash.

        On macOS, /bin/bash is version 3.2.  This test verifies
        the generated script avoids bash 4+ features.
        """
        config = _build_chain_config(tmp_path)
        create_seed_sentinels(config)
        seed_volume(config.volumes["src"])

        script = generate_script(
            config,
            ScriptOptions(config_path="test.yaml"),
        )

        result = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"/bin/bash -n failed:\n{result.stderr}"
