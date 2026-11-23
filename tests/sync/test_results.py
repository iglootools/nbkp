"""Tests for nbkp.sync.results.SyncResult."""

from nbkp.sync import SyncFailureKind, SyncOutcome, SyncResult
from tests.fakes import completed


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

    def test_succeeded_keeps_stderr(self) -> None:
        """Regression: a succeeded result kept only stdout, losing rsync's warnings."""
        proc = completed(0, stdout="sent 1 file\n", stderr="skipped special file\n")

        sr = SyncResult.succeeded("s1", dry_run=False, proc=proc)

        assert sr.output == "sent 1 file\nskipped special file\n"
