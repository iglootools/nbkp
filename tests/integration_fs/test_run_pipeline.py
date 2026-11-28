"""Integration tests: the run pipeline against real local hard-link snapshots."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR
from nbkp.policy import Strictness
from nbkp.run.pipeline import check_and_run
from nbkp.sync import (
    RunOptions,
    SyncFailureKind,
    SyncOutcome,
    SyncResult,
)
from nbkp.sync.testkit.seed import create_seed_sentinels

_NOW = datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC)


def _config(tmp_path: Path, max_snapshots: int | None = None) -> Config:
    volumes: dict[str, Volume] = {
        "src": LocalVolume(slug="src", path=str(tmp_path / "src")),
        "dst": LocalVolume(slug="dst", path=str(tmp_path / "dst")),
    }
    config = Config(
        volumes=volumes,
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True,
                    max_snapshots=max_snapshots,
                ),
            ),
        },
        syncs={"s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst")},
    )
    create_seed_sentinels(config)
    (tmp_path / "src" / "data.txt").write_text("hello")
    return config


def _snapshots(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "dst" / SNAPSHOTS_DIR).iterdir())


def _run(
    config: Config,
    *,
    dry_run: bool = False,
    now: datetime = _NOW,
) -> list[SyncResult]:
    pipeline = check_and_run(
        config,
        RunOptions(
            clock=lambda: now,
            platform=sys.platform,
            resolved_endpoints={},
            strictness=Strictness.IGNORE_INACTIVE,
            dry_run=dry_run,
        ),
    )
    assert not pipeline.has_preflight_errors, pipeline.preflight.sync_statuses
    return pipeline.results


class TestHardLinkDryRun:
    def test_dry_run_leaves_destination_untouched(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        # An orphan (newer than latest) that a real run would remove.
        _run(config)
        [first] = _snapshots(tmp_path)
        orphan = "2099-01-01T00-00-00.000Z"
        (tmp_path / "dst" / SNAPSHOTS_DIR / orphan).mkdir()

        results = _run(config, dry_run=True, now=datetime(2026, 3, 7, tzinfo=UTC))

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert _snapshots(tmp_path) == sorted([first, orphan])
        assert str(Path(tmp_path / "dst" / LATEST_LINK).readlink()) == (
            f"{SNAPSHOTS_DIR}/{first}"
        )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
class TestPruneReadOnlySnapshot:
    def test_snapshot_with_read_only_directories_is_pruned(
        self,
        tmp_path: Path,
    ) -> None:
        """`rsync -a` keeps read-only directory modes; pruning must still delete."""
        old = tmp_path / "dst" / SNAPSHOTS_DIR / "2020-01-01T00-00-00.000Z"
        (old / "ro" / "unlistable").mkdir(parents=True)
        (old / "ro" / "f").write_text("x")
        (old / "ro" / "unlistable").chmod(0o000)
        (old / "ro").chmod(0o555)
        config = _config(tmp_path, max_snapshots=1)
        latest = tmp_path / "dst" / LATEST_LINK
        latest.unlink(missing_ok=True)
        latest.symlink_to(f"{SNAPSHOTS_DIR}/{old.name}")

        results = _run(config)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].warnings == ()
        assert not old.exists()
        new = str(Path(tmp_path / "dst" / LATEST_LINK).readlink())
        assert (tmp_path / "dst" / new / "data.txt").read_text() == "hello"


class TestRsyncFailure:
    def test_new_snapshot_dir_removed(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        config = config.model_copy(
            update={
                "syncs": {
                    "s1": config.syncs["s1"].model_copy(
                        update={
                            "rsync_options": config.syncs[
                                "s1"
                            ].rsync_options.model_copy(
                                update={"extra_options": ["--no-such-option"]},
                            ),
                        },
                    ),
                },
            },
        )

        results = _run(config)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert results[0].warnings == ()
        assert _snapshots(tmp_path) == []


class TestClockBehindLatest:
    def test_complete_snapshots_survive(self, tmp_path: Path) -> None:
        """
        A run with the clock behind latest fails instead of moving latest back.

        So the next run does not remove the newer complete snapshots as orphans.
        """
        config = _config(tmp_path)
        _run(config)
        [complete] = _snapshots(tmp_path)

        behind = _run(config, now=datetime(2025, 1, 1, tzinfo=UTC))

        assert behind[0].failure == SyncFailureKind.MKDIR
        assert _snapshots(tmp_path) == [complete]
        assert str(Path(tmp_path / "dst" / LATEST_LINK).readlink()) == (
            f"{SNAPSHOTS_DIR}/{complete}"
        )

        later = _run(config, now=datetime(2026, 3, 7, tzinfo=UTC))

        assert later[0].outcome == SyncOutcome.SUCCESS
        assert complete in _snapshots(tmp_path)

    def test_dry_run_reports_the_refusal(self, tmp_path: Path) -> None:
        """A dry run must not report success for a run that will be refused."""
        config = _config(tmp_path)
        _run(config)
        [complete] = _snapshots(tmp_path)

        behind = _run(config, dry_run=True, now=datetime(2025, 1, 1, tzinfo=UTC))

        assert behind[0].failure == SyncFailureKind.MKDIR
        assert "is the clock behind?" in (behind[0].detail or "")
        assert _snapshots(tmp_path) == [complete]

    def test_same_name_as_latest_is_refused(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _run(config)
        [complete] = _snapshots(tmp_path)
        (tmp_path / "src" / "data.txt").write_text("changed")

        again = _run(config)

        assert again[0].failure == SyncFailureKind.MKDIR
        latest = tmp_path / "dst" / SNAPSHOTS_DIR / complete
        assert (latest / "data.txt").read_text() == "hello"
