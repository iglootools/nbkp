"""Tests for nbkp.sync.modes.hardlink: snapshot dirs, --link-dest, cleanup."""

from nbkp.config import Config, HardLinkSnapshotConfig
from nbkp.fsprotocol import Snapshot
from nbkp.sync.results import SyncFailureKind, SyncOutcome, SyncWarningKind
from tests.fakes import FakeCommandRunner, FakeRsync, completed
from tests.sync.runner_helpers import (
    NEW_SNAPSHOT_NAME,
    active_statuses,
    make_snapshot_config,
    run_syncs,
)


def _make_hard_link_config(max_snapshots: int | None = None) -> Config:
    return make_snapshot_config(
        hard_link=HardLinkSnapshotConfig(enabled=True, max_snapshots=max_snapshots)
    )


_NEW_SNAPSHOT = f"/dst/snapshots/{NEW_SNAPSHOT_NAME}"
_PREVIOUS = Snapshot.from_name("2026-03-05T10:00:00.000Z")
_ORPHAN = "2026-03-05T12:00:00.000Z"
_DENIED = completed(1, stderr="Permission denied")


def _fresh_destination() -> FakeCommandRunner:
    """A destination whose ``latest`` still points to /dev/null: no orphans."""
    return FakeCommandRunner().on("readlink", returns=completed(0, "/dev/null\n"))


def _destination_with_orphan() -> FakeCommandRunner:
    """``latest`` → _PREVIOUS, plus a newer snapshot left by a failed run."""
    return (
        FakeCommandRunner()
        .on("readlink", returns=completed(0, f"snapshots/{_PREVIOUS.name}\n"))
        .on("ls", returns=completed(0, f"{_PREVIOUS.name}\n{_ORPHAN}\n"))
    )


class TestHardLinkSync:
    def test_dry_run_never_modifies_destination(self) -> None:
        """No orphan cleanup, no snapshot dir, no latest update in dry-run."""
        config = _make_hard_link_config(max_snapshots=2)
        _, statuses = active_statuses(config)
        statuses = {
            k: v.model_copy(update={"destination_latest_snapshot": _PREVIOUS})
            for k, v in statuses.items()
        }
        rsync, runner = FakeRsync(), _destination_with_orphan()

        results = run_syncs(
            config, statuses, dry_run=True, rsync=rsync, run_command=runner
        )

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path is None
        assert runner.commands == []
        kwargs = rsync.last_kwargs
        assert kwargs["dry_run"] is True
        assert kwargs["dest_suffix"] == f"snapshots/{NEW_SNAPSHOT_NAME}"
        assert kwargs["link_dest"] == f"../{_PREVIOUS.name}"

    def test_dry_run_rsync_failure_removes_nothing(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        rsync = FakeRsync([completed(23, stderr="err")])
        runner = _fresh_destination()

        results = run_syncs(
            config, statuses, dry_run=True, rsync=rsync, run_command=runner
        )

        assert results[0].failure == SyncFailureKind.RSYNC
        assert runner.commands == []

    def test_real_run_names_snapshot_from_clock(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        rsync, runner = FakeRsync(), _fresh_destination()

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].warnings == ()
        assert rsync.last_kwargs["dest_suffix"] == f"snapshots/{NEW_SNAPSHOT_NAME}"
        assert runner.commands == [
            ["readlink", "/dst/latest"],
            ["mkdir", "-p", "/dst/snapshots"],
            ["mkdir", _NEW_SNAPSHOT],
            ["ln", "-sfn", f"snapshots/{NEW_SNAPSHOT_NAME}", "/dst/latest"],
        ]

    def test_orphans_removed_before_sync(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        runner = _destination_with_orphan()

        results = run_syncs(config, statuses, rsync=FakeRsync(), run_command=runner)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert runner.commands_starting_with("rm") == [
            ["rm", "-rf", f"/dst/snapshots/{_ORPHAN}"]
        ]

    def test_orphan_cleanup_failure_is_a_warning(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        runner = _destination_with_orphan().on("rm", returns=_DENIED)

        results = run_syncs(config, statuses, rsync=FakeRsync(), run_command=runner)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.ORPHAN_CLEANUP]

    def test_mkdir_failure(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        rsync = FakeRsync()
        runner = _fresh_destination().on("mkdir", returns=_DENIED)

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert results[0].failure == SyncFailureKind.MKDIR
        assert rsync.calls == []

    def test_refuses_snapshot_not_after_latest(self) -> None:
        """Same name as latest, or a clock behind it: no snapshot, no rsync."""
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        same = Snapshot.from_name(NEW_SNAPSHOT_NAME)  # what the clock would name
        statuses = {
            k: v.model_copy(update={"destination_latest_snapshot": same})
            for k, v in statuses.items()
        }
        rsync = FakeRsync()
        runner = (
            FakeCommandRunner()
            .on("readlink", returns=completed(0, f"snapshots/{same.name}\n"))
            .on("ls", returns=completed(0, f"{same.name}\n"))
        )

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert results[0].failure == SyncFailureKind.MKDIR
        assert "is the clock behind?" in (results[0].detail or "")
        assert runner.commands_starting_with("mkdir") == []
        assert runner.commands_starting_with("ln") == []
        assert rsync.calls == []

    def test_rsync_failure_removes_new_snapshot_dir(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        rsync = FakeRsync([completed(23, stderr="err")])
        runner = _fresh_destination()

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert results[0].rsync_exit_code == 23
        assert runner.commands_starting_with("rm") == [["rm", "-rf", _NEW_SNAPSHOT]]
        assert runner.commands_starting_with("ln") == []

    def test_snapshot_dir_removal_failure_is_a_warning(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        rsync = FakeRsync([OSError("rsync: not found")])
        runner = _fresh_destination().on("rm", returns=_DENIED)

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert [w.kind for w in results[0].warnings] == [
            SyncWarningKind.SNAPSHOT_DIR_CLEANUP
        ]

    def test_symlink_failure(self) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        runner = _fresh_destination().on("ln", returns=_DENIED)

        results = run_syncs(config, statuses, rsync=FakeRsync(), run_command=runner)

        assert results[0].failure == SyncFailureKind.SYMLINK

    def test_prune_failure_is_a_warning(self) -> None:
        """A complete snapshot stays a success when pruning fails."""
        config = _make_hard_link_config(max_snapshots=1)
        _, statuses = active_statuses(config)
        runner = (
            FakeCommandRunner()
            .on(
                "readlink",
                returns=[
                    completed(0, "/dev/null\n"),  # orphan cleanup, before rsync
                    completed(0, f"snapshots/{NEW_SNAPSHOT_NAME}\n"),  # prune
                ],
            )
            .on("ls", returns=completed(0, f"{_PREVIOUS.name}\n{NEW_SNAPSHOT_NAME}\n"))
            .on("rm", returns=_DENIED)
        )

        results = run_syncs(config, statuses, rsync=FakeRsync(), run_command=runner)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].pruned_paths is None
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.PRUNE]

    def test_no_prune_keeps_snapshots_beyond_max(self) -> None:
        """``run --no-prune``: retention is not applied even when exceeded."""
        config = _make_hard_link_config(max_snapshots=1)
        _, statuses = active_statuses(config)
        runner = (
            FakeCommandRunner()
            .on("readlink", returns=completed(0, "/dev/null\n"))
            .on("ls", returns=completed(0, f"{_PREVIOUS.name}\n{NEW_SNAPSHOT_NAME}\n"))
        )

        results = run_syncs(
            config, statuses, prune=False, rsync=FakeRsync(), run_command=runner
        )

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].pruned_paths is None
        assert runner.commands_starting_with("rm") == []
