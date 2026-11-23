"""Integration tests: generate scripts from chain configs and execute them."""

import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent

import pytest

from nbkp.cliwiring.lock import hold_run_lock, resolve_run_lock_path
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
from nbkp.policy import Strictness
from nbkp.run.pipeline import check_and_run
from nbkp.sh import ScriptOptions, generate_script
from nbkp.sync import RunOptions, SyncFailureKind
from nbkp.sync.testkit.seed import (
    SEED_EXCLUDE_FILTERS,
    create_seed_sentinels,
    seed_volume,
)

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)


def _has_bash4() -> bool:
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
    """
    Build a local-only 2-hop chain: src -> stage (hard-link) -> dst.

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
            slug="ep-stage",
            volume="stage",
            hard_link_snapshots=hl,
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
        tmp_path,
        step_1_options=step_1_options,
        max_snapshots=max_snapshots,
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
        # The run lock goes under the test's directory, not the developer's.
        env={**(env or os.environ), "XDG_STATE_HOME": str(_state_home(tmp_path))},
    )


def _state_home(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _with_flock(tmp_path: Path) -> dict[str, str]:
    """
    Return the environment, with ``flock`` on ``PATH``.

    Where util-linux's flock(1) is missing (macOS), a stand-in makes the
    same ``flock(2)`` call on the descriptor the script passes.
    """
    if shutil.which("flock") is not None:
        return dict(os.environ)
    bin_dir = tmp_path / "flock-bin"
    bin_dir.mkdir()
    flock = bin_dir / "flock"
    flock.write_text(
        dedent(f"""\
            #!/bin/sh
            # flock -n FD
            exec {shlex.quote(sys.executable)} -c '
            import fcntl, sys
            try:
                fcntl.flock(int(sys.argv[2]), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sys.exit(1)
            ' "$@"
        """),
        encoding="utf-8",
    )
    flock.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _stage(tmp_path: Path) -> Path:
    return tmp_path / _STAGE_DIR


def _snapshot_names(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (_stage(tmp_path) / SNAPSHOTS_DIR).iterdir())


def _write_rm_refusing(bin_dir: Path, name: str) -> None:
    """
    Write an ``rm`` on *bin_dir* that fails for paths containing *name*.

    While ``latest`` points to /dev/null, orphan cleanup removes every
    snapshot; this keeps one in place, as a cleanup that failed would.
    """
    fake_rm = bin_dir / "rm"
    fake_rm.write_text(
        dedent(f"""\
            #!/bin/sh
            case "$*" in *{name}*) exit 1 ;; esac
            exec {shutil.which("rm")} "$@"
        """),
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)


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
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_chain_config_valid_bash32(self, tmp_path: Path) -> None:
        """
        Generated script passes syntax check with /bin/bash.

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
        assert str(Path(latest).readlink()) == f"{SNAPSHOTS_DIR}/{snapshot}"
        assert (latest / "sample.txt").is_file()
        assert (tmp_path / _DST_DIR / "sample.txt").is_file()
        assert not (tmp_path / _DST_DIR / "excluded").exists()

    def test_orphaned_snapshot_is_removed(self, tmp_path: Path) -> None:
        """A snapshot newer than ``latest`` (left by a failed run) is deleted."""
        config = _seeded_chain(tmp_path)
        snapshots = _stage(tmp_path) / SNAPSHOTS_DIR
        previous, orphan = "2020-01-01T00-00-00.000Z", "2021-01-01T00-00-00.000Z"
        (snapshots / previous).mkdir()
        (snapshots / orphan).mkdir()
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink(missing_ok=True)
        latest.symlink_to(f"{SNAPSHOTS_DIR}/{previous}")

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert f"Removing orphaned snapshot: {orphan}" in result.stderr
        assert not (snapshots / orphan).exists()
        assert (snapshots / previous).exists()

    def test_first_run_leftover_is_removed_when_latest_is_dev_null(
        self,
        tmp_path: Path,
    ) -> None:
        """Before the first complete snapshot, every snapshot is a leftover."""
        config = _seeded_chain(tmp_path)
        snapshots = _stage(tmp_path) / SNAPSHOTS_DIR
        leftover = "2021-01-01T00-00-00.000Z"
        (snapshots / leftover).mkdir()
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink(missing_ok=True)
        latest.symlink_to("/dev/null")

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert f"Removing orphaned snapshot: {leftover}" in result.stderr
        assert not (snapshots / leftover).exists()
        assert len(_snapshot_names(tmp_path)) == 1

    def test_held_run_lock_stops_the_script(self, tmp_path: Path) -> None:
        """The script and nbkp run share one lock: no two runs at once."""
        config = _seeded_chain(tmp_path)
        environ = {"XDG_STATE_HOME": str(_state_home(tmp_path))}

        with hold_run_lock(resolve_run_lock_path(environ, Path.home())):
            result = _run_script(config, tmp_path, env=_with_flock(tmp_path))

        assert result.returncode == 1
        assert (
            "another nbkp command (a run, a mount, a prune, …) is in progress"
            in result.stderr
        )
        assert _snapshot_names(tmp_path) == []

    def test_free_run_lock_lets_the_script_run(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)

        result = _run_script(config, tmp_path, env=_with_flock(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "run lock" not in result.stderr

    @pytest.mark.skipif(shutil.which("flock") is not None, reason="flock installed")
    def test_runs_unlocked_without_flock(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert "WARN: flock not found" in result.stderr

    def test_dry_run_does_not_take_the_run_lock(self, tmp_path: Path) -> None:
        config = _seeded_chain(tmp_path)
        environ = {"XDG_STATE_HOME": str(_state_home(tmp_path))}

        with hold_run_lock(resolve_run_lock_path(environ, Path.home())):
            result = _run_script(config, tmp_path, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert "run lock" not in result.stderr

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
        assert str(Path(_stage(tmp_path) / LATEST_LINK).readlink()) == "/dev/null"
        # step-2 reads stage's latest -> /dev/null: pending, not a failure
        assert "SKIPPED step-2: source snapshot pending (dry-run)" in result.stderr

    def test_rsync_failure_stops_sync_and_cancels_downstream(
        self,
        tmp_path: Path,
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
        assert str(Path(_stage(tmp_path) / LATEST_LINK).readlink()) == "/dev/null"
        assert _snapshot_names(tmp_path) == []  # new snapshot dir removed
        assert not (tmp_path / _DST_DIR / "sample.txt").exists()

    def test_failure_after_rsync_stops_sync(self, tmp_path: Path) -> None:
        """
        Any failing step aborts the sync, not only rsync.

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
        assert str(Path(_stage(tmp_path) / LATEST_LINK).readlink()) == "/dev/null"

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

    @staticmethod
    def _with_step_1_disabled(tmp_path: Path) -> Config:
        """
        Run the chain once, then disable step-1 and remove the downstream copy.

        After the run, step-2 has a source snapshot.
        """
        chain = _seeded_chain(tmp_path)
        assert _run_script(chain, tmp_path).returncode == 0
        (tmp_path / _DST_DIR / "sample.txt").unlink()
        return chain.model_copy(
            update={
                "syncs": {
                    **chain.syncs,
                    "step-1": chain.syncs["step-1"].model_copy(
                        update={"enabled": False},
                    ),
                },
            },
        )

    def test_disabled_upstream_cancels_downstream_as_in_run(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Script and ``nbkp run`` agree: a disabled sync's downstream is cancelled.

        It is not run against a stale or missing source.
        """
        config = self._with_step_1_disabled(tmp_path)

        result = _run_script(config, tmp_path)
        pipeline = check_and_run(
            config,
            RunOptions(
                clock=lambda: _NOW, platform=sys.platform, resolved_endpoints={}
            ),
        )

        assert "SKIPPED step-1: disabled" in result.stderr
        assert "CANCELLED step-2: upstream sync is disabled" in result.stderr
        assert result.returncode == 0, result.stderr
        assert not (tmp_path / _DST_DIR / "sample.txt").exists()
        failures = {r.sync_slug: r.failure for r in pipeline.results}
        assert failures == {
            "step-1": SyncFailureKind.DISABLED,
            "step-2": SyncFailureKind.UPSTREAM_DISABLED,
        }

    def test_disabled_upstream_never_fails_the_run(self, tmp_path: Path) -> None:
        """
        Under ignore-none too, a disabled sync and its cancellations never fail.

        A cancellation is as severe as its cause, in the script as in ``nbkp run``.
        """
        config = self._with_step_1_disabled(tmp_path)

        result = _run_script(config, tmp_path, "--strictness", "ignore-none")
        pipeline = check_and_run(
            config,
            RunOptions(
                clock=lambda: _NOW,
                platform=sys.platform,
                resolved_endpoints={},
                strictness=Strictness.IGNORE_NONE,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert not pipeline.has_preflight_errors
        assert not pipeline.has_sync_failures

    def test_inactive_upstream_cancellation_fails_under_ignore_none(
        self,
        tmp_path: Path,
    ) -> None:
        """ignore-none aborts on the inactive sync before anything runs."""
        config = _seeded_chain(tmp_path)
        (tmp_path / _SRC_DIR / VOLUME_SENTINEL).unlink()

        result = _run_script(config, tmp_path, "--strictness", "ignore-none")

        assert result.returncode == 1

    def test_ignore_all_attempts_sync_with_infrastructure_errors(
        self,
        tmp_path: Path,
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

    def test_prune_keeps_snapshots_when_latest_cannot_be_read(
        self,
        tmp_path: Path,
    ) -> None:
        """
        As in `nbkp run`: an unreadable latest stops pruning, not its guard.

        The fake readlink fails once latest names a snapshot, i.e. only in
        the prune step. Without the guard, the new snapshot (sorting before
        the future-dated one) would be pruned from under latest.
        """
        config = _seeded_chain(tmp_path, max_snapshots=1)
        future = "2099-01-01T00-00-00.000Z"
        (_stage(tmp_path) / SNAPSHOTS_DIR / future).mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        flaky_readlink = bin_dir / "readlink"
        flaky_readlink.write_text(
            dedent(f"""\
                #!/bin/sh
                target=$({shutil.which("readlink")} "$@") || exit $?
                case "$target" in {SNAPSHOTS_DIR}/*) exit 1 ;; esac
                printf '%s\\n' "$target"
            """),
            encoding="utf-8",
        )
        flaky_readlink.chmod(0o755)
        _write_rm_refusing(bin_dir, future)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

        result = _run_script(config, tmp_path, env=env)

        assert result.returncode == 0, result.stderr
        assert "WARN: pruning failed" in result.stderr
        assert len(_snapshot_names(tmp_path)) == 2

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

    def test_latest_outside_snapshots_dir_aborts(self, tmp_path: Path) -> None:
        """Like ``nbkp run``: a timestamp-named target elsewhere is invalid."""
        config = _seeded_chain(tmp_path)
        assert _run_script(config, tmp_path).returncode == 0
        [snapshot] = _snapshot_names(tmp_path)
        lookalike = _stage(tmp_path) / "elsewhere" / snapshot
        lookalike.mkdir(parents=True)
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink()
        latest.symlink_to(f"elsewhere/{snapshot}")

        result = _run_script(config, tmp_path)

        assert result.returncode == 1
        assert "latest symlink target is invalid" in result.stderr
        assert "Starting sync" not in result.stderr

    def test_prune_never_keeps_extra_over_latest(self, tmp_path: Path) -> None:
        """
        Prune drops latest from the candidates before taking the oldest.

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
        assert str(Path(_stage(tmp_path) / LATEST_LINK).readlink()) == (
            f"{SNAPSHOTS_DIR}/{snapshot}"
        )

    def test_ignores_lookalike_snapshot_names(self, tmp_path: Path) -> None:
        """Only the exact names nbkp writes are pruned or removed as orphans."""
        config = _seeded_chain(tmp_path, max_snapshots=1)
        snapshots = _stage(tmp_path) / SNAPSHOTS_DIR
        previous = "2020-01-01T00-00-00.000Z"
        (snapshots / previous).mkdir()
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink()
        latest.symlink_to(f"{SNAPSHOTS_DIR}/{previous}")
        lookalikes = ["2020-01-01T00", "2099-03-07T09:00:00"]
        for name in lookalikes:
            (snapshots / name).mkdir()

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert "Pruning snapshot: 2020-01-01T00-00-00.000Z" in result.stderr
        for name in lookalikes:
            assert (snapshots / name).is_dir()

    def _mixed_name_forms(self, tmp_path: Path) -> tuple[str, str]:
        """
        Create an older Linux snapshot and a newer macOS one that latest points to.

        Linux names use a colon, macOS ones a hyphen: ':' sorts after '-', so name order
        inverts them.
        """
        older, newer = "2020-01-01T10:15:00.000Z", "2020-01-01T10-30-00.000Z"
        for name in (older, newer):
            (_stage(tmp_path) / SNAPSHOTS_DIR / name).mkdir()
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink()
        latest.symlink_to(f"{SNAPSHOTS_DIR}/{newer}")
        return older, newer

    def test_orphan_cleanup_keeps_older_snapshot_of_other_name_form(
        self,
        tmp_path: Path,
    ) -> None:
        config = _seeded_chain(tmp_path)
        older, newer = self._mixed_name_forms(tmp_path)

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        assert {older, newer} <= set(_snapshot_names(tmp_path))

    def test_prune_removes_oldest_snapshot_when_name_forms_are_mixed(
        self,
        tmp_path: Path,
    ) -> None:
        config = _seeded_chain(tmp_path, max_snapshots=2)
        older, newer = self._mixed_name_forms(tmp_path)

        result = _run_script(config, tmp_path)

        assert result.returncode == 0, result.stderr
        names = _snapshot_names(tmp_path)
        assert older not in names
        assert newer in names
        assert len(names) == 2

    def test_refuses_snapshot_not_after_latest(self, tmp_path: Path) -> None:
        """With the clock behind latest, latest is not moved back in time."""
        config = _seeded_chain(tmp_path)
        future = "2099-01-01T00-00-00.000Z"
        (_stage(tmp_path) / SNAPSHOTS_DIR / future).mkdir()
        latest = _stage(tmp_path) / LATEST_LINK
        latest.unlink()
        latest.symlink_to(f"{SNAPSHOTS_DIR}/{future}")

        result = _run_script(config, tmp_path)

        assert result.returncode == 1
        assert "is the clock behind?" in result.stderr
        assert _snapshot_names(tmp_path) == [future]
        assert str(Path(latest).readlink()) == f"{SNAPSHOTS_DIR}/{future}"

    def test_existing_snapshot_dir_is_not_reused(self, tmp_path: Path) -> None:
        """
        Mkdir without -p: a name collision fails the sync.

        Here the collision is with a leftover that orphan cleanup failed to remove, so a
        failed rsync never removes a snapshot it did not create.
        """
        config = _seeded_chain(tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        # `date` always answers the same second, so every run collides.
        fixed = "2026-02-21T12:00:00.000Z"
        if sys.platform == "darwin":
            fixed = fixed.replace(":", "-")
        fake_date = fake_bin / "date"
        fake_date.write_text(f"#!/bin/sh\necho '{fixed}'\n")
        fake_date.chmod(0o755)
        _write_rm_refusing(fake_bin, fixed)
        existing = _stage(tmp_path) / SNAPSHOTS_DIR / fixed
        existing.mkdir()
        (existing / "keep.txt").write_text("keep")
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

        result = _run_script(config, tmp_path, env=env)

        assert result.returncode == 1
        assert (existing / "keep.txt").read_text() == "keep"
