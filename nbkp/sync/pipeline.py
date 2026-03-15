"""Check-then-run pipeline: preflight checks followed by sync execution.

Composes ``check_all_syncs`` and ``run_all_syncs`` into a single
reusable function shared by the CLI ``run`` command and integration
tests.  Display/output, mount lifecycle, and config loading are the
caller's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..config import Config
from ..config.epresolution import ResolvedEndpoints
from ..preflight import SyncError, SyncStatus, VolumeStatus, check_all_syncs
from .rsync import ProgressMode
from .runner import SyncResult, run_all_syncs

INACTIVE_ERRORS: frozenset[SyncError] = frozenset(
    {
        SyncError.SRC_EP_SENTINEL_NOT_FOUND,
        SyncError.DST_EP_SENTINEL_NOT_FOUND,
        SyncError.SRC_VOL_UNAVAILABLE,
        SyncError.DST_VOL_UNAVAILABLE,
        SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING,
    }
)
"""Sync errors that represent expected inactivity (missing sentinels,
unavailable volumes) rather than real failures."""


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a check-then-run pipeline execution."""

    vol_statuses: dict[str, VolumeStatus]
    sync_statuses: dict[str, SyncStatus]
    results: list[SyncResult]
    """Empty when preflight found fatal errors and syncs were not executed."""
    has_preflight_errors: bool
    has_sync_failures: bool
    """True when any sync failed for a reason other than expected inactivity."""


def is_expected_skip(
    result: SyncResult,
    sync_statuses: dict[str, SyncStatus],
    inactive_errors: frozenset[SyncError] = INACTIVE_ERRORS,
) -> bool:
    """Return True if a failed sync result is an expected inactive skip."""
    ss = sync_statuses.get(result.sync_slug)
    return ss is not None and bool(ss.errors) and set(ss.errors) <= inactive_errors


def has_fatal_errors(
    sync_statuses: dict[str, SyncStatus],
    *,
    strict: bool = False,
    inactive_errors: frozenset[SyncError] = INACTIVE_ERRORS,
) -> bool:
    """Return True if any sync has errors that should abort the run.

    When *strict* is True, any inactive sync (including missing
    sentinels) is fatal.  Otherwise, only errors outside
    *inactive_errors* are fatal.
    """
    return (
        any(not s.active for s in sync_statuses.values())
        if strict
        else any(set(s.errors) - inactive_errors for s in sync_statuses.values())
    )


def check_and_run(
    config: Config,
    *,
    strict: bool = False,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_check_progress: Callable[[str], None] | None = None,
    on_checks_done: (
        Callable[[dict[str, VolumeStatus], dict[str, SyncStatus]], None] | None
    ) = None,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observations: dict[str, Any] | None = None,
    inactive_errors: frozenset[SyncError] = INACTIVE_ERRORS,
) -> PipelineResult:
    """Run preflight checks, then execute syncs if no fatal errors.

    This is the core "check → run" pipeline shared by the CLI ``run``
    command and integration tests.  It does **not** include
    display/output logic, mount lifecycle, or config loading — those
    remain the caller's responsibility.

    Parameters
    ----------
    strict:
        When True, any inactive sync (including missing sentinels) is
        treated as a fatal preflight error.
    on_check_progress:
        Called for each volume/endpoint checked (progress tracking).
    on_checks_done:
        Called after preflight completes but before syncs start.
        Fires regardless of whether there are fatal errors, so the
        CLI can print the check table in both cases.
    inactive_errors:
        Set of ``SyncError`` values that represent expected inactivity.
    """
    vol_statuses, sync_statuses = check_all_syncs(
        config,
        on_progress=on_check_progress,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
    )

    if on_checks_done is not None:
        on_checks_done(vol_statuses, sync_statuses)

    preflight_errors = has_fatal_errors(
        sync_statuses, strict=strict, inactive_errors=inactive_errors
    )

    if preflight_errors:
        return PipelineResult(
            vol_statuses=vol_statuses,
            sync_statuses=sync_statuses,
            results=[],
            has_preflight_errors=True,
            has_sync_failures=True,
        )

    results = run_all_syncs(
        config,
        sync_statuses,
        dry_run=dry_run,
        only_syncs=only_syncs,
        progress=progress,
        prune=prune,
        on_rsync_output=on_rsync_output,
        on_sync_start=on_sync_start,
        on_sync_end=on_sync_end,
        resolved_endpoints=resolved_endpoints,
    )

    sync_failures = any(
        not r.success and not is_expected_skip(r, sync_statuses, inactive_errors)
        for r in results
    )

    return PipelineResult(
        vol_statuses=vol_statuses,
        sync_statuses=sync_statuses,
        results=results,
        has_preflight_errors=False,
        has_sync_failures=sync_failures,
    )
