import pytest

from nbkp.preflight import SyncError
from nbkp.snapshots.errors import SnapshotOp
from nbkp.snapshots.models import SnapshotErrorKind, SnapshotResultError
from nbkp.snapshots.output import snapshot_error_text


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
