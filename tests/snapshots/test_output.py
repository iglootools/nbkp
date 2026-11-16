"""Tests for the human output of `snapshots show` / `snapshots prune`."""

from collections.abc import Callable, Sequence
from io import StringIO
from typing import Any

import pytest
from rich.console import Console

from nbkp.fsprotocol import Snapshot
from nbkp.preflight import SyncError
from nbkp.snapshots.errors import SnapshotOp
from nbkp.snapshots.models import (
    PruneResult,
    ShowResult,
    SnapshotErrorKind,
    SnapshotResultError,
    SnapshotSkipReason,
)
from nbkp.snapshots.output import (
    print_human_prune_results,
    print_human_show_results,
    snapshot_error_text,
)

_SNAP = Snapshot.from_name("2026-03-06T14:30:00.000Z")
# Remote stderr often ends in a bracketed tag that Rich markup would drop.
_STDERR = "ls: cannot access (code 23) [sender=3.4.1]"
_ERROR = SnapshotResultError(
    kind=SnapshotErrorKind.OPERATION_FAILED,
    operation=SnapshotOp.LIST,
    path="/dst/snapshots",
    message=_STDERR,
)
_FAILED = f"FAILED (list snapshots failed for /dst/snapshots: {_STDERR})"


def _render(
    print_fn: Callable[..., None], results: Sequence[object], **kwargs: Any
) -> str:
    buffer = StringIO()
    print_fn(results, console=Console(file=buffer, width=200), **kwargs)
    return buffer.getvalue()


def _row(output: str, slug: str) -> str:
    return next(line for line in output.splitlines() if f" {slug} " in line)


class TestShowOutput:
    def test_status_cells(self) -> None:
        output = _render(
            print_human_show_results,
            [
                ShowResult(
                    sync_slug="ok-sync",
                    snapshot_mode="hard-link",
                    snapshots=(_SNAP,),
                    latest=_SNAP,
                    max_snapshots=3,
                ),
                ShowResult(
                    sync_slug="off-sync",
                    snapshot_mode="none",
                    snapshots=(),
                    latest=None,
                    max_snapshots=None,
                    skip_reason=SnapshotSkipReason.NO_SNAPSHOTS,
                ),
                ShowResult(
                    sync_slug="bad-sync",
                    snapshot_mode="btrfs",
                    snapshots=(),
                    latest=None,
                    max_snapshots=None,
                    error=_ERROR,
                ),
            ],
        )

        ok = _row(output, "ok-sync")
        assert "hard-link" in ok
        assert _SNAP.name in ok
        assert "keep 3" in ok
        assert "OK" in ok
        assert "SKIPPED (no snapshots configured)" in _row(output, "off-sync")
        bad = _row(output, "bad-sync")
        assert "unlimited" in bad
        assert _FAILED in bad


class TestPruneOutput:
    def test_status_cells(self) -> None:
        output = _render(
            print_human_prune_results,
            [
                PruneResult(
                    sync_slug="ok-sync",
                    deleted=("/dst/snapshots/a", "/dst/snapshots/b"),
                    kept=3,
                    dry_run=True,
                ),
                PruneResult(
                    sync_slug="off-sync",
                    deleted=(),
                    kept=0,
                    dry_run=True,
                    skip_reason=SnapshotSkipReason.INACTIVE,
                ),
                PruneResult(
                    sync_slug="bad-sync",
                    deleted=(),
                    kept=0,
                    dry_run=True,
                    error=_ERROR,
                ),
            ],
            dry_run=True,
        )

        assert "NBKP prune (dry run):" in output
        ok = _row(output, "ok-sync")
        assert " 2 " in ok
        assert " 3 " in ok
        assert "OK" in ok
        assert "SKIPPED (inactive)" in _row(output, "off-sync")
        assert _FAILED in _row(output, "bad-sync")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            SnapshotResultError(
                kind=SnapshotErrorKind.INACTIVE,
                preflight_errors=(SyncError.SOURCE_ENDPOINT_INACTIVE,),
            ),
            f"inactive: {SyncError.SOURCE_ENDPOINT_INACTIVE.value}",
        ),
        (
            SnapshotResultError(
                kind=SnapshotErrorKind.PREFLIGHT_ERRORS,
                preflight_errors=(
                    SyncError.DESTINATION_ENDPOINT_INACTIVE,
                    SyncError.ENDPOINT_HOST_ERRORS,
                ),
            ),
            (
                "preflight errors: destination endpoint inactive,"
                f" {SyncError.ENDPOINT_HOST_ERRORS.value}"
            ),
        ),
        (
            SnapshotResultError(
                kind=SnapshotErrorKind.OPERATION_FAILED,
                operation=SnapshotOp.LIST,
                path="/dst/snapshots",
                message="Permission denied",
            ),
            "list snapshots failed for /dst/snapshots: Permission denied",
        ),
    ],
)
def test_snapshot_error_text(error: SnapshotResultError, expected: str) -> None:
    assert snapshot_error_text(error) == expected
