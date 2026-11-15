"""Tests for nbkp.sync.modes.btrfs: snapshot after rsync into staging, pruning."""

from nbkp.config import BtrfsSnapshotConfig, Config
from nbkp.sync.results import SyncFailureKind, SyncOutcome
from tests.fakes import FakeCommandRunner, FakeRsync, completed
from tests.sync.runner_helpers import (
    NEW_SNAPSHOT_NAME,
    active_statuses,
    make_remote_same_server_btrfs_config,
    make_snapshot_config,
    run_syncs,
)

_NEW_SNAPSHOT = f"/dst/snapshots/{NEW_SNAPSHOT_NAME}"


def _btrfs_config(max_snapshots: int | None = None) -> Config:
    return make_snapshot_config(
        btrfs=BtrfsSnapshotConfig(enabled=True, max_snapshots=max_snapshots)
    )


class TestBtrfsSync:
    def test_btrfs_snapshot_after_sync(self) -> None:
        config = _btrfs_config()
        _, sync_statuses = active_statuses(config)
        rsync, runner = FakeRsync(), FakeCommandRunner()

        results = run_syncs(config, sync_statuses, rsync=rsync, run_command=runner)

        assert results[0].success is True
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert rsync.last_kwargs["dest_suffix"] == "staging"
        assert runner.commands == [
            ["btrfs", "subvolume", "snapshot", "-r", "/dst/staging", _NEW_SNAPSHOT],
            ["ln", "-sfn", f"snapshots/{NEW_SNAPSHOT_NAME}", "/dst/latest"],
        ]

    def test_btrfs_snapshot_skipped_on_dry_run(self) -> None:
        config = _btrfs_config()
        _, sync_statuses = active_statuses(config)
        runner = FakeCommandRunner()

        results = run_syncs(
            config, sync_statuses, dry_run=True, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].success is True
        assert results[0].snapshot_path is None
        assert runner.commands == []

    def test_btrfs_no_link_dest(self) -> None:
        config = _btrfs_config()
        _, sync_statuses = active_statuses(config)
        rsync = FakeRsync()

        run_syncs(config, sync_statuses, rsync=rsync, run_command=FakeCommandRunner())

        assert rsync.last_kwargs["link_dest"] is None

    def test_remote_same_server_with_btrfs(self) -> None:
        config = make_remote_same_server_btrfs_config()
        _, sync_statuses = active_statuses(config)
        runner = FakeCommandRunner()

        results = run_syncs(
            config, sync_statuses, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].success is True
        assert results[0].snapshot_path == f"/backup/snapshots/{NEW_SNAPSHOT_NAME}"
        assert runner.commands_starting_with("ln") == [
            ["ln", "-sfn", f"snapshots/{NEW_SNAPSHOT_NAME}", "/backup/latest"]
        ]

    def test_snapshot_failure(self) -> None:
        config = _btrfs_config()
        _, sync_statuses = active_statuses(config)
        runner = FakeCommandRunner().on(
            "btrfs", "subvolume", "snapshot", returns=completed(1, stderr="no space")
        )

        results = run_syncs(
            config, sync_statuses, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].outcome == SyncOutcome.FAILED
        assert results[0].failure == SyncFailureKind.SNAPSHOT
        assert runner.commands_starting_with("ln") == []

    def test_auto_prune_after_snapshot(self) -> None:
        config = _btrfs_config(max_snapshots=1)
        _, sync_statuses = active_statuses(config)
        old = "2026-03-05T10:00:00.000Z"
        runner = (
            FakeCommandRunner()
            .on("ls", returns=completed(0, f"{old}\n{NEW_SNAPSHOT_NAME}\n"))
            .on("readlink", returns=completed(0, f"snapshots/{NEW_SNAPSHOT_NAME}\n"))
        )

        results = run_syncs(
            config, sync_statuses, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].success is True
        assert results[0].pruned_paths == (f"/dst/snapshots/{old}",)
        assert runner.commands_starting_with("btrfs", "subvolume", "delete") == [
            ["btrfs", "subvolume", "delete", f"/dst/snapshots/{old}"]
        ]

    def test_no_auto_prune_without_max_snapshots(self) -> None:
        config = _btrfs_config()
        _, sync_statuses = active_statuses(config)
        runner = FakeCommandRunner()

        results = run_syncs(
            config, sync_statuses, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].success is True
        assert results[0].pruned_paths is None
        assert runner.commands_starting_with("ls") == []
