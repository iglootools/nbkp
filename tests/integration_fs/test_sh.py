"""Integration tests: generate scripts from chain configs and execute them."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, VOLUME_SENTINEL
from nbkp.sh import ScriptOptions, generate_script
from nbkp.sync.testkit.seed import (
    SEED_EXCLUDE_FILTERS,
    create_seed_sentinels,
    seed_volume,
)

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)


def _has_bash4() -> bool:
    """Check whether a bash 4+ binary is available."""
    bash = shutil.which("bash")
    if bash is None:
        return False
    r = subprocess.run(
        [bash, "-c", "declare -A x=()"],
        capture_output=True,
        check=False,
    )
    return r.returncode == 0


_requires_bash4 = pytest.mark.skipif(
    not _has_bash4(),
    reason="bash 4+ not available",
)

# Volume directory names exercise quoting: spaces, a literal `$`, a quote.
_SRC_DIR = "s r$c"
_STAGE_DIR = "st'age"
_DST_DIR = "d s t"


def _build_chain_config(
    tmp_path: Path,
    *,
    step_1_options: RsyncOptions | None = None,
    max_snapshots: int | None = None,
) -> Config:
    """Build a local-only 2-hop chain: src -> stage (hard-link) -> dst.

    Mirrors the structure of the Docker chain test but
    restricted to local volumes (no SSH/remote).
    """
    hl = HardLinkSnapshotConfig(enabled=True, max_snapshots=max_snapshots)

    volumes: dict[str, LocalVolume | RemoteVolume] = {
        "src": LocalVolume(slug="src", path=str(tmp_path / _SRC_DIR)),
        "stage": LocalVolume(slug="stage", path=str(tmp_path / _STAGE_DIR)),
        "dst": LocalVolume(slug="dst", path=str(tmp_path / _DST_DIR)),
    }
    sync_endpoints: dict[str, SyncEndpoint] = {
        "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
        "ep-stage": SyncEndpoint(
            slug="ep-stage", volume="stage", hard_link_snapshots=hl
        ),
        "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
    }
    syncs: dict[str, SyncConfig] = {
        "step-1": SyncConfig(
            slug="step-1",
            source="ep-src",
            destination="ep-stage",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=step_1_options or RsyncOptions(),
        ),
        "step-2": SyncConfig(
            slug="step-2",
            source="ep-stage",
            destination="ep-dst",
            filters=SEED_EXCLUDE_FILTERS,
        ),
    }
    return Config(volumes=volumes, sync_endpoints=sync_endpoints, syncs=syncs)


def _seeded_chain(
    tmp_path: Path,
    *,
    step_1_options: RsyncOptions | None = None,
    max_snapshots: int | None = None,
) -> Config:
    config = _build_chain_config(
        tmp_path, step_1_options=step_1_options, max_snapshots=max_snapshots
    )
    create_seed_sentinels(config)
    seed_volume(config.volumes["src"])
    return config


def _run_script(
    config: Config,
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Write the generated script to a file and execute it."""
    script = generate_script(
        config,
        ScriptOptions(platform=sys.platform, config_path="test.yaml"),
        now=_NOW,
    )
    script_path = tmp_path / "backup.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        [str(script_path), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _stage(tmp_path: Path) -> Path:
    return tmp_path / _STAGE_DIR


def _snapshot_names(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (_stage(tmp_path) / SNAPSHOTS_DIR).iterdir())


class TestGeneratedScriptSyntax:
    @_requires_bash4
    def test_chain_config_valid_bash_no_portable(self, tmp_path: Path) -> None:
        """--no-portable script passes bash -n (bash 4+)."""
        config = _seeded_chain(tmp_path)
        script = generate_script(
            config,
            ScriptOptions(platform=sys.platform, config_path="t.yaml", portable=False),
            now=_NOW,
        )
        result = subprocess.run(
            ["bash", "-n"], input=script, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_chain_config_valid_bash32(self, tmp_path: Path) -> None:
        """Generated script passes syntax check with /bin/bash.

        On macOS, /bin/bash is version 3.2.  This test verifies
        the generated script avoids bash 4+ features.
        """
        config = _seeded_chain(tmp_path)
        script = generate_script(
            config,
            ScriptOptions(platform=sys.platform, config_path="test.yaml"),
            now=_NOW,
        )
        result = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"/bin/bash -n failed:\n{result.stderr}"


class TestGeneratedScriptExecution:
    def test_chain_succeeds(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert "All syncs completed successfully" in result.stderr
        [snapshot] = _snapshot_names(tmp_path)
        latest = _stage(tmp_path) / LATEST_LINK
        assert os.readlink(latest) == f"{SNAPSHOTS_DIR}/{snapshot}"
        assert (latest / "sample.txt").is_file()
        assert (tmp_path / _DST_DIR / "sample.txt").is_file()
        assert not (tmp_path / _DST_DIR / "excluded").exists()

    def test_progress_flags_reach_rsync(self, tmp_path: Path) -> None:
        """--progress modes pass several rsync flags as separate words."""
        config = _seeded_chain(tmp_path)

        result = _run_script(config, tmp_path, "--progress", "per-file")

        assert result.returncode == 0, result.stderr
        assert "sample.txt" in result.stdout

    def test_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)

        result = _run_script(config, tmp_path, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert _snapshot_names(tmp_path) == []
        assert os.readlink(_stage(tmp_path) / LATEST_LINK) == "/dev/null"
        # step-2 reads stage's latest -> /dev/null: pending, not a failure
        assert "SKIPPED step-2: source snapshot pending (dry-run)" in result.stderr

    def test_rsync_failure_stops_sync_and_cancels_downstream(
        self, tmp_path: Path
    ) -> None:
        """A failed rsync must not snapshot, move latest, or run downstream."""
        config = _seeded_chain(
            tmp_path,
            step_1_options=RsyncOptions(extra_options=["--no-such-rsync-option"]),
        )

        result = _run_script(config, tmp_path)

        assert result.returncode != 0
        assert "ERROR: rsync exited with code" in result.stderr
        assert "FAILED step-1" in result.stderr
        assert "Completed sync: step-1" not in result.stderr
        assert "CANCELLED step-2" in result.stderr
        # step-2 is itself active, so its cancellation counts (as in run)
        assert "2 sync(s) failed" in result.stderr
        assert os.readlink(_stage(tmp_path) / LATEST_LINK) == "/dev/null"
        assert _snapshot_names(tmp_path) == []  # new snapshot dir removed
        assert not (tmp_path / _DST_DIR / "sample.txt").exists()

    def test_failure_after_rsync_stops_sync(self, tmp_path: Path) -> None:
        """Any failing step aborts the sync, not only rsync.

        Sync functions are invoked from a `||`-free context in a `set -e`
        subshell; in a `||` context bash would disable errexit and carry on.
        """
        config = _seeded_chain(tmp_path, max_snapshots=1)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        failing_ln = bin_dir / "ln"
        failing_ln.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        failing_ln.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

        result = _run_script(config, tmp_path, env=env)

        assert result.returncode != 0
        assert "FAILED step-1" in result.stderr
        assert "Completed sync: step-1" not in result.stderr
        assert "CANCELLED step-2" in result.stderr
        assert os.readlink(_stage(tmp_path) / LATEST_LINK) == "/dev/null"

    def test_missing_volume_sentinel_skips_sync(self, tmp_path: Path) -> None:
        """Like `nbkp run`: a missing .nbkp-vol makes the sync inactive."""
        config = _seeded_chain(tmp_path)
        (tmp_path / _DST_DIR / VOLUME_SENTINEL).unlink()

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert "SKIPPED step-2: inactive" in result.stderr
        assert "Completed sync: step-1" in result.stderr
        assert not (tmp_path / _DST_DIR / "sample.txt").exists()

    def test_inactive_upstream_cancels_downstream(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)
        (tmp_path / _SRC_DIR / VOLUME_SENTINEL).unlink()

        result = _run_script(config, tmp_path)

        assert "SKIPPED step-1: inactive" in result.stderr
        assert "CANCELLED step-2: upstream sync was skipped" in result.stderr
        assert not (tmp_path / _DST_DIR / "sample.txt").exists()
        # Rooted in expected inactivity: not a failure, as in `nbkp run`.
        assert result.returncode == 0, result.stderr

    def test_inactive_upstream_cancellation_fails_under_ignore_none(
        self, tmp_path: Path
    ) -> None:
        """ignore-none aborts on the inactive sync before anything runs."""
        config = _seeded_chain(tmp_path)
        (tmp_path / _SRC_DIR / VOLUME_SENTINEL).unlink()

        result = _run_script(config, tmp_path, "--strictness", "ignore-none")

        assert result.returncode == 1

    def test_ignore_all_attempts_sync_with_infrastructure_errors(
        self, tmp_path: Path
    ) -> None:
        """Sentinels present, snapshots/ missing: attempted, and mkdir -p heals it."""
        config = _seeded_chain(tmp_path)
        (_stage(tmp_path) / SNAPSHOTS_DIR).rmdir()

        result = _run_script(config, tmp_path, "--strictness", "ignore-all")

        assert result.returncode == 0, result.stderr
        assert "preflight errors ignored (ignore-all), attempting" in result.stderr
        assert len(_snapshot_names(tmp_path)) == 1

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
    def test_prune_failure_is_a_warning(self, tmp_path: Path) -> None:
        """As in `nbkp run`: the new snapshot stands when pruning fails."""
        config = _seeded_chain(tmp_path, max_snapshots=1)
        old = _stage(tmp_path) / SNAPSHOTS_DIR / "2020-01-01T00-00-00.000Z"
        (old / "sub").mkdir(parents=True)
        (old / "sub" / "f").write_text("x")
        (old / "sub").chmod(0o555)
        try:
            result = _run_script(config, tmp_path)
        finally:
            (old / "sub").chmod(0o755)

        assert result.returncode == 0, result.stderr
        assert "WARN: pruning failed" in result.stderr
        assert "Completed sync: step-1" in result.stderr
        assert old.exists()
        assert (tmp_path / _DST_DIR / "sample.txt").exists()

    def test_strictness_ignore_none_aborts_on_inactive(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)
        (tmp_path / _DST_DIR / VOLUME_SENTINEL).unlink()

        result = _run_script(config, tmp_path, "--strictness", "ignore-none")

        assert result.returncode == 1
        assert "Aborting: preflight checks found errors in 1 sync(s)" in result.stderr
        assert "Starting sync" not in result.stderr

    def test_infrastructure_error_aborts_before_any_sync(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)
        (_stage(tmp_path) / SNAPSHOTS_DIR).rmdir()

        result = _run_script(config, tmp_path)

        assert result.returncode == 1
        assert "snapshots/ directory not found" in result.stderr
        assert "Starting sync" not in result.stderr

    def test_prune_never_keeps_extra_over_latest(self, tmp_path: Path) -> None:
        """Prune drops latest from the candidates before taking the oldest.

        A snapshot sorting after the new one (future-dated) must be the one
        pruned, as `prune_snapshots` does, not silently kept.
        """
        config = _seeded_chain(tmp_path, max_snapshots=1)
        future = "2099-01-01T00-00-00.000Z"
        (_stage(tmp_path) / SNAPSHOTS_DIR / future).mkdir()

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        [snapshot] = _snapshot_names(tmp_path)
        assert snapshot != future
        assert os.readlink(_stage(tmp_path) / LATEST_LINK) == (
            f"{SNAPSHOTS_DIR}/{snapshot}"
        )
