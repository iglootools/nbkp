"""The result of a sync: its outcome, why it did not succeed, and its warnings."""

import subprocess
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from nbkp.policy import Severity, Strictness, classify_severity
from nbkp.preflight import SyncErrorKind


class SyncOutcome(StrEnum):
    """
    How a sync ended.

    Succeeded, failed in a step that ran, cancelled because an upstream sync did
    not succeed, or skipped by preflight.
    """

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SyncFailureKind(StrEnum):
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
    DISABLED = "disabled"
    """The sync is ``enabled: false``: never run, and never fatal."""

    # CANCELLED: an upstream sync did not succeed.
    UPSTREAM_FAILED = "upstream-failed"
    """An upstream sync failed (directly or transitively)."""
    UPSTREAM_SKIPPED = "upstream-skipped"
    """An upstream sync was skipped (directly or transitively)."""
    UPSTREAM_DISABLED = "upstream-disabled"
    """An upstream sync is disabled (directly or transitively)."""


_OUTCOME_BY_FAILURE: dict[SyncFailureKind, SyncOutcome] = {
    SyncFailureKind.RSYNC: SyncOutcome.FAILED,
    SyncFailureKind.MKDIR: SyncOutcome.FAILED,
    SyncFailureKind.SNAPSHOT: SyncOutcome.FAILED,
    SyncFailureKind.SYMLINK: SyncOutcome.FAILED,
    SyncFailureKind.INACTIVE: SyncOutcome.SKIPPED,
    SyncFailureKind.DRY_RUN_PENDING: SyncOutcome.SKIPPED,
    SyncFailureKind.PREFLIGHT: SyncOutcome.SKIPPED,
    SyncFailureKind.DISABLED: SyncOutcome.SKIPPED,
    SyncFailureKind.UPSTREAM_FAILED: SyncOutcome.CANCELLED,
    SyncFailureKind.UPSTREAM_SKIPPED: SyncOutcome.CANCELLED,
    SyncFailureKind.UPSTREAM_DISABLED: SyncOutcome.CANCELLED,
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
    },
)

#: Non-runs rooted in a disabled sync: never fatal, under any strictness,
#: since nobody asked for that sync to run.
DISABLED_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {SyncFailureKind.DISABLED, SyncFailureKind.UPSTREAM_DISABLED},
)

#: Non-runs rooted in expected inactivity.
_INACTIVE_FAILURES: frozenset[SyncFailureKind] = frozenset(
    {
        SyncFailureKind.INACTIVE,
        SyncFailureKind.DRY_RUN_PENDING,
        SyncFailureKind.UPSTREAM_SKIPPED,
    },
)


class SyncWarningKind(StrEnum):
    """A best-effort step that failed without failing the sync."""

    ORPHAN_CLEANUP = "orphan-cleanup"
    PRUNE = "prune"
    SNAPSHOT_DIR_CLEANUP = "snapshot-dir-cleanup"


class SyncWarning(BaseModel):
    """A best-effort step of a sync that failed, and the message saying how."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SyncWarningKind
    message: str


class SyncResult(BaseModel):
    """
    The result of one sync.

    ``failure`` says why a sync did not succeed and determines ``outcome``; ``detail``
    describes a failed or cancelled step, and ``preflight_errors`` the errors that kept
    a skipped sync from running. Prefer the constructors (:meth:`succeeded`,
    :meth:`failed`, :meth:`skipped`, :meth:`cancelled`), which keep the two consistent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    outcome: SyncOutcome
    dry_run: bool
    rsync_exit_code: int = -1
    """``-1`` when rsync did not run to completion."""
    output: str = ""
    """rsync's merged stdout and stderr; only their last 65,536 characters."""
    failure: SyncFailureKind | None = None
    cancelled_by: str | None = None
    """For a cancelled sync, the upstream sync whose result caused it."""
    detail: str | None = None
    preflight_errors: tuple[SyncErrorKind, ...] = ()
    """For a skipped sync, its preflight errors (cascade pointers included)."""
    snapshot_path: str | None = None
    pruned_paths: tuple[str, ...] | None = None
    warnings: tuple[SyncWarning, ...] = ()

    @computed_field
    @property
    def success(self) -> bool:
        """Return whether the sync succeeded; serialized with the result."""
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
        *,
        dry_run: bool,
        proc: subprocess.CompletedProcess[str],
        snapshot_path: str | None = None,
        pruned_paths: tuple[str, ...] | None = None,
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        """Return the result of a sync whose rsync run *proc* succeeded."""
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
        *,
        dry_run: bool,
        failure: SyncFailureKind,
        detail: str,
        rsync_exit_code: int = -1,
        output: str = "",
        warnings: tuple[SyncWarning, ...] = (),
    ) -> SyncResult:
        """
        Return the result of a sync that ran and failed at the step *failure* names.

        *detail* describes the failure; *rsync_exit_code* and *output* are rsync's,
        when rsync ran.
        """
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
        slug: str,
        *,
        dry_run: bool,
        failure: SyncFailureKind,
        preflight_errors: tuple[SyncErrorKind, ...],
    ) -> SyncResult:
        """Return the result of a sync that *preflight_errors* kept from running."""
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.SKIPPED,
            dry_run=dry_run,
            failure=failure,
            preflight_errors=preflight_errors,
        )

    @staticmethod
    def cancelled(
        slug: str,
        *,
        dry_run: bool,
        failure: SyncFailureKind,
        upstream: str,
    ) -> SyncResult:
        """
        Return the result of a sync cancelled because *upstream* did not succeed.

        *failure* says whether *upstream* failed, was skipped or is disabled; the
        detail message names it.
        """
        match failure:
            case SyncFailureKind.UPSTREAM_FAILED:
                reason = "failed"
            case SyncFailureKind.UPSTREAM_DISABLED:
                reason = "is disabled"
            case _:
                reason = "skipped"
        return SyncResult(
            sync_slug=slug,
            outcome=SyncOutcome.CANCELLED,
            dry_run=dry_run,
            failure=failure,
            cancelled_by=upstream,
            detail=f"Cancelled: upstream sync '{upstream}' {reason}",
        )

    def with_warnings(self, warnings: tuple[SyncWarning, ...]) -> SyncResult:
        """Return a copy with *warnings* appended."""
        return self.model_copy(update={"warnings": (*self.warnings, *warnings)})


def result_severity(
    result: SyncResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """
    Return the severity of a sync's result under *strictness*.

    Runtime failures, and cancellations caused by one, are errors under every
    strictness. Non-runs rooted in expected inactivity follow the strictness policy for
    inactive errors, and syncs skipped for infrastructure errors follow the policy for
    infrastructure errors. A disabled sync, and what it cancels, is never more than a
    warning. ``ERROR`` is exactly what makes ``run`` exit non-zero.
    """
    match result.failure:
        case None:
            return Severity.OK
        case kind if kind in RUNTIME_FAILURES:
            return Severity.ERROR
        case kind if kind in DISABLED_FAILURES:
            return Severity.WARNING
        case kind:
            return classify_severity(
                is_inactive=kind in _INACTIVE_FAILURES,
                strictness=strictness,
            )
