"""Tests for nbkp.sync.results.SyncResult."""

from nbkp.sync import SyncFailureKind, SyncOutcome, SyncResult


class TestSyncResult:
    def test_construction_defaults(self) -> None:
        sr = SyncResult(
            sync_slug="s1",
            outcome=SyncOutcome.SUCCESS,
            dry_run=False,
            rsync_exit_code=0,
            output="done",
        )
        assert sr.snapshot_path is None
        assert sr.detail is None

    def test_construction_full(self) -> None:
        sr = SyncResult(
            sync_slug="s1",
            outcome=SyncOutcome.FAILED,
            dry_run=False,
            rsync_exit_code=1,
            output="",
            failure=SyncFailureKind.RSYNC,
            detail="failed",
        )
        assert sr.detail == "failed"
        assert sr.success is False
