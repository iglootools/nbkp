"""Shared test logic for btrfs component tests (local and remote).

Provides ``BtrfsEnv`` — a fixture-friendly bundle of config, setup
callbacks, and verification callbacks — and plain test functions that
exercise the btrfs snapshot module against either execution backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from nbkp.config import Config, SyncConfig
from nbkp.config.epresolution import ResolvedEndpoints
from nbkp.fsprotocol import Snapshot
from nbkp.sync.snapshots.btrfs import (
    create_snapshot,
    delete_snapshot,
    prune_snapshots,
)
from nbkp.sync.snapshots.common import (
    get_latest_snapshot,
    list_snapshots,
    update_latest_symlink,
)


@dataclass(frozen=True)
class BtrfsEnv:
    """Everything a btrfs component test needs, abstracting local vs remote."""

    sync: SyncConfig
    config: Config
    resolved: ResolvedEndpoints
    create_staging: Callable[[], None]
    seed_staging: Callable[[str], None]
    check_exists: Callable[[str], bool]
    check_readonly: Callable[[str], bool]


# ── Shared test functions ────────────────────────────────────────────


def run_test_creates_readonly_snapshot(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    snapshot_path = create_snapshot(
        env.sync, env.config, resolved_endpoints=env.resolved
    )

    assert env.check_exists(snapshot_path)
    assert env.check_readonly(snapshot_path)


def run_test_lists_sorted_oldest_first(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

    create_snapshot(env.sync, env.config, now=now1, resolved_endpoints=env.resolved)
    create_snapshot(env.sync, env.config, now=now2, resolved_endpoints=env.resolved)

    snapshots = list_snapshots(env.sync, env.config, env.resolved)
    assert len(snapshots) == 2
    assert "2024-01-01" in snapshots[0].name
    assert "2024-01-02" in snapshots[1].name


def run_test_returns_most_recent(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)

    create_snapshot(env.sync, env.config, now=now1, resolved_endpoints=env.resolved)
    create_snapshot(env.sync, env.config, now=now2, resolved_endpoints=env.resolved)

    latest = get_latest_snapshot(env.sync, env.config, env.resolved)
    assert latest is not None
    assert "2024-01-02" in latest.name


def run_test_returns_none_when_empty(env: BtrfsEnv) -> None:
    latest = get_latest_snapshot(env.sync, env.config, env.resolved)
    assert latest is None


def run_test_deletes_subvolume(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    snapshot_path = create_snapshot(
        env.sync, env.config, resolved_endpoints=env.resolved
    )
    assert env.check_exists(snapshot_path)

    dst_vol = env.config.volumes["dst"]
    delete_snapshot(snapshot_path, dst_vol, env.resolved)

    assert not env.check_exists(snapshot_path)


def run_test_prunes_oldest_beyond_limit(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    names = []
    for i in range(3):
        now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
        path = create_snapshot(
            env.sync, env.config, now=now, resolved_endpoints=env.resolved
        )
        names.append(Snapshot.from_path(path).name)

    update_latest_symlink(
        env.sync,
        env.config,
        Snapshot.from_name(names[-1]),
        resolved_endpoints=env.resolved,
    )

    deleted = prune_snapshots(env.sync, env.config, 1, resolved_endpoints=env.resolved)
    assert len(deleted) == 2

    remaining = list_snapshots(env.sync, env.config, env.resolved)
    assert len(remaining) == 1
    assert names[-1] == remaining[0].name


def run_test_dry_run_preserves_all(env: BtrfsEnv) -> None:
    env.create_staging()
    env.seed_staging("test data")

    for i in range(3):
        now = datetime(2024, 1, 1 + i, tzinfo=timezone.utc)
        path = create_snapshot(
            env.sync, env.config, now=now, resolved_endpoints=env.resolved
        )
    snapshot = Snapshot.from_path(path)
    update_latest_symlink(
        env.sync, env.config, snapshot, resolved_endpoints=env.resolved
    )

    deleted = prune_snapshots(
        env.sync, env.config, 1, dry_run=True, resolved_endpoints=env.resolved
    )
    assert len(deleted) == 2

    remaining = list_snapshots(env.sync, env.config, env.resolved)
    assert len(remaining) == 3
