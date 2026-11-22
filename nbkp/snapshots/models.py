import enum

from pydantic import BaseModel, ConfigDict, computed_field, field_serializer

from nbkp.fsprotocol import Snapshot
from nbkp.preflight import SyncErrorKind
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError


class SnapshotSkipReason(enum.StrEnum):
    """Why a sync was left out of ``snapshots show`` / ``snapshots prune``.

    A skip is never a failure: failures go in the result's ``error``.
    """

    NO_SNAPSHOTS = "no-snapshots"
    NO_MAX_SNAPSHOTS = "no-max-snapshots"
    INACTIVE = "inactive"
    PREFLIGHT_ERRORS = "preflight-errors"
    """Infrastructure errors under ``--strictness ignore-all``."""


class SnapshotErrorKind(enum.StrEnum):
    INACTIVE = "inactive"
    """Expected inactivity, fatal under ``--strictness ignore-none``."""
    PREFLIGHT_ERRORS = "preflight-errors"
    OPERATION_FAILED = "operation-failed"
    """Listing, reading ``latest`` or deleting a snapshot failed."""


class SnapshotResultError(BaseModel):
    """Why a sync failed in ``snapshots show`` / ``snapshots prune``.

    The preflight errors and the snapshot operation serialize as their
    stable codes (enum member names); the output layer renders the text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SnapshotErrorKind
    preflight_errors: tuple[SyncErrorKind, ...] = ()
    operation: SnapshotOp | None = None
    path: str | None = None
    message: str | None = None
    """The failed operation's stderr or error message."""

    @staticmethod
    def from_operation(error: SnapshotOperationError) -> SnapshotResultError:
        return SnapshotResultError(
            kind=SnapshotErrorKind.OPERATION_FAILED,
            operation=error.op,
            path=error.path,
            message=error.stderr.strip(),
        )

    @field_serializer("preflight_errors")
    def _serialize_codes(self, errors: tuple[SyncErrorKind, ...]) -> list[str]:
        return [e.name for e in errors]

    @field_serializer("operation")
    def _serialize_operation(self, op: SnapshotOp | None) -> str | None:
        return op.name if op is not None else None


class PruneResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    deleted: tuple[str, ...]
    kept: int
    dry_run: bool
    skip_reason: SnapshotSkipReason | None = None
    error: SnapshotResultError | None = None

    @computed_field
    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None


class ShowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    snapshot_mode: str
    snapshots: tuple[Snapshot, ...]
    latest: Snapshot | None
    max_snapshots: int | None
    skip_reason: SnapshotSkipReason | None = None
    error: SnapshotResultError | None = None

    @computed_field
    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None
