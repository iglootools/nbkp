"""Integration tests: the run pipeline against real local hard-link snapshots."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
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
from nbkp.sync import SyncFailureKind, SyncOutcome, SyncResult, SyncWarningKind
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
                    enabled=True, max_snapshots=max_snapshots
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


def _run(config: Config, *, dry_run: bool = False) -> list[SyncResult]:
    pipeline = check_and_run(
        config,
        clock=lambda: _NOW,
        platform=sys.platform,
        strictness=Strictness.IGNORE_INACTIVE,
        dry_run=dry_run,
    )
    assert not pipeline.has_preflight_errors, pipeline.sync_statuses
    return pipeline.results


class TestHardLinkDryRun:
    def test_dry_run_leaves_destination_untouched(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        # An orphan (newer than latest) that a real run would remove.
        _run(config)
        [first] = _snapshots(tmp_path)
        orphan = "2099-01-01T00-00-00.000Z"
        (tmp_path / "dst" / SNAPSHOTS_DIR / orphan).mkdir()

        results = _run(config, dry_run=True)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert _snapshots(tmp_path) == sorted([first, orphan])
        assert os.readlink(tmp_path / "dst" / LATEST_LINK) == (
            f"{SNAPSHOTS_DIR}/{first}"
        )


@pytest.fixture
def undeletable_snapshot(tmp_path: Path) -> Iterator[Path]:
    """An old snapshot whose content cannot be removed (read-only dir)."""
    old = tmp_path / "dst" / SNAPSHOTS_DIR / "2020-01-01T00-00-00.000Z"
    (old / "sub").mkdir(parents=True)
    (old / "sub" / "f").write_text("x")
    (old / "sub").chmod(0o555)
    yield old
    (old / "sub").chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
class TestPruneFailure:
    def test_prune_failure_is_a_warning(
        self, tmp_path: Path, undeletable_snapshot: Path
    ) -> None:
        config = _config(tmp_path, max_snapshots=1)
        results = _run(config)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].failure is None
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.PRUNE]
        assert undeletable_snapshot.exists()
        new = os.readlink(tmp_path / "dst" / LATEST_LINK)
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
                                update={"extra_options": ["--no-such-option"]}
                            )
                        }
                    )
                }
            }
        )

        results = _run(config)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert results[0].warnings == ()
        assert _snapshots(tmp_path) == []
