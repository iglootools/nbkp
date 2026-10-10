"""Tests for nbkp.sync.modes.hardlink: snapshot dirs, --link-dest, cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.snapshots.errors import SnapshotOp
from nbkp.sync.results import SyncFailureKind, SyncOutcome, SyncWarningKind
from tests.sync.runner_helpers import (
    FIXED_NOW,
    active_statuses,
    ok_proc,
    run_syncs,
    snapshot_error,
)


def _make_hard_link_config(max_snapshots: int | None = None) -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    return Config(
        volumes={"src": src, "dst": dst},
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


_NEW_SNAPSHOT = "/dst/snapshots/2026-03-06T14:30:00.000Z"


_PREVIOUS = Snapshot.from_name("2026-03-05T10:00:00.000Z")


@patch("nbkp.sync.modes.hardlink.hl_prune_snapshots")
@patch("nbkp.sync.modes.common.update_latest_symlink")
@patch("nbkp.sync.modes.hardlink.hl_delete_snapshot")
@patch("nbkp.sync.modes.hardlink.create_snapshot_dir")
@patch("nbkp.sync.modes.hardlink.cleanup_orphaned_snapshots")
@patch("nbkp.sync.modes.common.run_rsync")
class TestHardLinkSync:
    def test_dry_run_never_modifies_destination(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        """No orphan cleanup, no snapshot dir, no latest update in dry-run."""
        config = _make_hard_link_config(max_snapshots=2)
        _, statuses = active_statuses(config)
        statuses = {
            k: v.model_copy(update={"destination_latest_snapshot": _PREVIOUS})
            for k, v in statuses.items()
        }
        mock_rsync.return_value = ok_proc()

        results = run_syncs(config, statuses, dry_run=True)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path is None
        mock_cleanup.assert_not_called()
        mock_mkdir.assert_not_called()
        mock_delete.assert_not_called()
        mock_symlink.assert_not_called()
        mock_prune.assert_not_called()
        kwargs = mock_rsync.call_args.kwargs
        assert kwargs["dry_run"] is True
        assert kwargs["dest_suffix"] == "snapshots/2026-03-06T14:30:00.000Z"
        assert kwargs["link_dest"] == f"../{_PREVIOUS.name}"

    def test_dry_run_rsync_failure_removes_nothing(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="err")

        results = run_syncs(config, statuses, dry_run=True)

        assert results[0].failure == SyncFailureKind.RSYNC
        mock_delete.assert_not_called()

    def test_real_run_names_snapshot_from_clock(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_rsync.return_value = ok_proc()
        mock_mkdir.return_value = _NEW_SNAPSHOT

        results = run_syncs(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].warnings == ()
        mock_cleanup.assert_called_once()
        assert mock_mkdir.call_args.kwargs["now"] == FIXED_NOW
        assert mock_mkdir.call_args.kwargs["platform"] == "linux"
        mock_symlink.assert_called_once()
        mock_prune.assert_not_called()  # no max-snapshots

    def test_orphan_cleanup_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_cleanup.side_effect = snapshot_error(SnapshotOp.DELETE)
        mock_rsync.return_value = ok_proc()
        mock_mkdir.return_value = _NEW_SNAPSHOT

        results = run_syncs(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.ORPHAN_CLEANUP]

    def test_mkdir_failure(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_mkdir.side_effect = snapshot_error(SnapshotOp.MKDIR)

        results = run_syncs(config, statuses)

        assert results[0].failure == SyncFailureKind.MKDIR
        mock_rsync.assert_not_called()

    def test_rsync_failure_removes_new_snapshot_dir(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="err")

        results = run_syncs(config, statuses)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert results[0].rsync_exit_code == 23
        assert mock_delete.call_args.args[0] == _NEW_SNAPSHOT
        mock_symlink.assert_not_called()

    def test_snapshot_dir_removal_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.side_effect = OSError("rsync: not found")
        mock_delete.side_effect = snapshot_error(SnapshotOp.DELETE)

        results = run_syncs(config, statuses)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert [w.kind for w in results[0].warnings] == [
            SyncWarningKind.SNAPSHOT_DIR_CLEANUP
        ]

    def test_symlink_failure(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = ok_proc()
        mock_symlink.side_effect = snapshot_error(SnapshotOp.UPDATE_LATEST)

        results = run_syncs(config, statuses)

        assert results[0].failure == SyncFailureKind.SYMLINK

    def test_prune_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        """A complete snapshot stays a success when pruning fails."""
        config = _make_hard_link_config(max_snapshots=2)
        _, statuses = active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = ok_proc()
        mock_prune.side_effect = snapshot_error(SnapshotOp.DELETE)

        results = run_syncs(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].pruned_paths is None
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.PRUNE]
