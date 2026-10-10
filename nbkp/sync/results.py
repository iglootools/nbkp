"""Sync results: outcomes, failure and warning kinds, and their severity."""

from __future__ import annotations

import subprocess
from enum import Enum

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from ..policy import Severity, Strictness, classify_severity


class SyncOutcome(str, Enum):
    """Outcome of a sync operation."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SyncFailureKind(str, Enum):
    """Why a sync did not succeed.  ``None`` on a successful result."""

    # FAILED: the sync ran and a step failed.
    RSYNC = "rsync"
    """rsync exited non-zero, or could not be started (SSH, spawn)."""
    MKDIR = "mkdir"
    """The hard-link snapshot directory could not be created."""
    SNAPSHOT = "snapshot"
    """The btrfs snapshot could not be taken."""
    SYMLINK = "symlink"
    """The ``latest`` symlink could not be updated."""

    # SKIPPED: preflight kept the sync from running.
    INACTIVE = "inactive"
    """Expected inactivity: a missing sentinel, an unreachable host."""
    DRY_RUN_PENDING = "dry-run-pending"
    """Dry run: the source snapshot only exists once the upstream really runs."""
    PREFLIGHT = "preflight"
    """Infrastructure errors, and presence could not be proven."""

    # CANCELLED: an upstream sync did not succeed.
    UPSTREAM_FAILED = "upstream-failed"
    """An upstream sync failed (directly or transitively)."""
    UPSTREAM_SKIPPED = "upstream-skipped"
    """An upstream sync was skipped (directly or transitively)."""


_OUTCOME_BY_FAILURE: dict[SyncFailureKind, SyncOutcome] = {
    SyncFailureKind.RSYNC: SyncOutcome.FAILED,
    SyncFailureKind.MKDIR: SyncOutcome.FAILED,
    SyncFailureKind.SNAPSHOT: SyncOutcome.FAILED,
    SyncFailureKind.SYMLINK: SyncOutcome.FAILED,
    SyncFailureKind.INACTIVE: SyncOutcome.SKIPPED,
    SyncFailureKind.DRY_RUN_PENDING: SyncOutcome.SKIPPED,
    SyncFailureKind.PREFLIGHT: SyncOutcome.SKIPPED,
    SyncFailureKind.UPSTREAM_FAILED: SyncOutcome.CANCELLED,
    SyncFailureKind.UPSTREAM_SKIPPED: SyncOutcome.CANCELLED,
}

#: Failures that originate in a step that actually ran (or in a cascade
#: from one): fatal under every strictness.
RUNTIME_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {
        SyncFailureKind.RSYNC,
        SyncFailureKind.MKDIR,
        SyncFailureKind.SNAPSHOT,
        SyncFailureKind.SYMLINK,
        SyncFailureKind.UPSTREAM_FAILED,
    }
)

#: Non-runs rooted in expected inactivity.
_INACTIVE_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {
        SyncFailureKind.INACTIVE,
        SyncFailureKind.DRY_RUN_PENDING,
        SyncFailureKind.UPSTREAM_SKIPPED,
    }
)


class SyncWarningKind(str, Enum):
    """A best-effort step that failed without failing the sync."""

    ORPHAN_CLEANUP = "orphan-cleanup"
    PRUNE = "prune"
    SNAPSHOT_DIR_CLEANUP = "snapshot-dir-cleanup"


class SyncWarning(BaseModel):
    """A non-fatal problem encountered while running a sync."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SyncWarningKind
    message: str


class SyncResult(BaseModel):
    """Result of running a sync.

    ``failure`` says why a sync did not succeed and determines
    ``outcome``; ``detail`` is the human-readable rendering of it.
    Prefer the constructors (:meth:`succeeded`, :meth:`failed`,
    :meth:`skipped`, :meth:`cancelled`), which keep the two consistent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    outcome: SyncOutcome
    dry_run: bool
    rsync_exit_code: int = -1
    """``-1`` when rsync did not run to completion."""
    output: str = ""
    failure: SyncFailureKind | None = None
    cancelled_by: str | None = None
    """For a cancelled sync, the upstream sync whose result caused it."""
    detail: str | None = None
    snapshot_path: str | None = None
    pruned_paths: tuple[str, ...] | None = None
    warnings: tuple[SyncWarning, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.outcome is SyncOutcome.SUCCESS

    @model_validator(mode="after")
    def _check_consistency(self) -> SyncResult:
        expected = (
            SyncOutcome.SUCCESS
            if self.failure is None
            else _OUTCOME_BY_FAILURE[self.failure]
        )
        if self.outcome is not expected:
            msg = f"outcome {self.outcome.value} contradicts failure {self.failure}"
            raise ValueError(msg)
        if (self.cancelled_by is not None) != (expected is SyncOutcome.CANCELLED):
            msg = "cancelled_by is set exactly when the sync is cancelled"
            raise ValueError(msg)
        return self

    @staticmethod
    def succeeded(
        slug: str,
        dry_run: bool,
        proc: subprocess.CompletedProcess[str],
        *,
        snapshot_path: str | None = None,
        pruned_paths: tuple[str, ...] | None = None,
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.SUCCESS,
            dry_run=dry_run,
            rsync_exit_code=proc.returncode,
            output=proc.stdout,
            snapshot_path=snapshot_path,
            pruned_paths=pruned_paths,
            warnings=warnings,
        )

    @staticmethod
    def failed(
        slug: str,
        dry_run: bool,
        failure: SyncFailureKind,
        detail: str,
        *,
        rsync_exit_code: int = -1,
        output: str = "",
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.FAILED,
            dry_run=dry_run,
            rsync_exit_code=rsync_exit_code,
            output=output,
            failure=failure,
            detail=detail,
            warnings=warnings,
        )

    @staticmethod
    def skipped(
        slug: str, dry_run: bool, failure: SyncFailureKind, detail: str
    ) -> SyncResult:
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.SKIPPED,
            dry_run=dry_run,
            failure=failure,
            detail=detail,
        )

    @staticmethod
    def cancelled(
        slug: str, dry_run: bool, failure: SyncFailureKind, upstream: str
    ) -> SyncResult:
        reason = "failed" if failure is SyncFailureKind.UPSTREAM_FAILED else "skipped"
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.CANCELLED,
            dry_run=dry_run,
            failure=failure,
            cancelled_by=upstream,
            detail=f"Cancelled: upstream sync '{upstream}' {reason}",
        )

    def with_warnings(self, warnings: tuple[SyncWarning, ...]) -> SyncResult:
        """A copy with *warnings* appended."""
        return self.model_copy(update={"warnings": (*self.warnings, *warnings)})


def result_severity(
    result: SyncResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Severity of a sync result under *strictness*.

    Runtime failures — and cancellations caused by one — are errors under
    every strictness.  Non-runs rooted in expected inactivity follow the
    strictness policy for inactive errors, and syncs skipped for
    infrastructure errors follow the policy for infrastructure errors.
    ``ERROR`` is exactly what makes ``run`` exit non-zero.
    """
    match result.failure:
        case None:
            return Severity.OK
        case kind if kind in RUNTIME_FAILURES:
            return Severity.ERROR
        case kind:
            return classify_severity(kind in _INACTIVE_FAILURES, strictness)
