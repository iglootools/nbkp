"""Check-then-run pipeline: preflight checks followed by sync execution.

Composes ``check_all_syncs`` and ``run_all_syncs`` into a single
reusable function shared by the CLI ``run`` command and integration
tests.  Display/output, mount lifecycle, and config loading are the
caller's responsibility.
"""

import shutil
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from ..config import Config
from ..disks import MountObservation, UmountResult, any_failed
from ..policy import Severity, Strictness
from ..preflight import (
    PreflightErrorKind,
    PreflightResult,
    SyncErrorKind,
    SyncStatus,
    check_all_syncs,
    has_fatal_errors,
    is_fatal,
)
from ..remote.dispatch import CommandLocator
from ..sync.orchestration import RunOptions, run_all_syncs
from ..sync.results import SyncResult, result_severity

__all__ = [
    "PipelineResult",
    "SyncCallbacks",
    "check_and_run",
    "fatal_sync_errors",
    "run_checked",
    "run_failed",
    "sync_failed",
]


@dataclass(frozen=True)
class PipelineResult:
    preflight: PreflightResult
    results: list[SyncResult]
    """Empty when preflight found fatal errors and syncs were not executed."""
    has_preflight_errors: bool
    has_sync_failures: bool
    """True when any result is fatal under the strictness (see ``sync_failed``);
    False when preflight aborted the run, as no sync ran."""


@dataclass(frozen=True)
class SyncCallbacks:
    on_rsync_output: Callable[[str], None] | None = None
    on_sync_start: Callable[[str], None] | None = None
    on_sync_end: Callable[[str, SyncResult], None] | None = None


def sync_failed(result: SyncResult, strictness: Strictness) -> bool:
    """Whether *result* makes the run fail under *strictness*.

    Runtime failures, and cancellations caused by one, always do.  Skips
    and cancellations rooted in expected inactivity only do under
    ``ignore-none``; skips for infrastructure errors do unless
    ``ignore-all``.  A disabled sync, and what it cancels, never does.
    """
    return result_severity(result, strictness) is Severity.ERROR


def check_and_run(
    config: Config,
    options: RunOptions,
    *,
    on_check_start: Callable[[str], None] | None = None,
    on_check_end: Callable[[str, Sequence[PreflightErrorKind]], None] | None = None,
    on_checks_done: Callable[[PreflightResult], None] | None = None,
    callbacks: SyncCallbacks | None = None,
    mount_observations: dict[str, MountObservation] | None = None,
    exclude_locations: Collection[str] = (),
    which: CommandLocator = shutil.which,
) -> PipelineResult:
    """Run preflight checks, then execute syncs if no fatal errors.

    Parameters
    ----------
    options:
        The run's choices, shared by preflight and the syncs; its
        ``strictness`` controls how preflight errors are treated (see
        :class:`~nbkp.policy.Strictness`).
    on_check_start / on_check_end:
        Called before / after each check with a label (e.g.
        ``"ssh:localhost"``) and, after, the check's errors.
    on_checks_done:
        Called after preflight completes but before syncs start, whether
        or not there are fatal errors, so the CLI can print the check
        table in both cases.
    which:
        Finds tools on the orchestrator.
    """
    preflight = check_all_syncs(
        config,
        on_check_start=on_check_start,
        on_check_end=on_check_end,
        only_syncs=options.only_syncs,
        resolved_endpoints=options.resolved_endpoints,
        dry_run=options.dry_run,
        mount_observations=mount_observations,
        exclude_locations=exclude_locations,
        run=options.run_command,
        which=which,
    )
    if on_checks_done is not None:
        on_checks_done(preflight)
    return run_checked(config, preflight, options, callbacks)


def run_checked(
    config: Config,
    preflight: PreflightResult,
    options: RunOptions,
    callbacks: SyncCallbacks | None = None,
) -> PipelineResult:
    """The second half of :func:`check_and_run`: abort when *preflight*
    found errors fatal under the options' strictness, else run the syncs and
    judge their results.
    """
    strictness = options.strictness
    if has_fatal_errors(preflight.sync_statuses, strictness=strictness):
        return PipelineResult(
            preflight=preflight,
            results=[],
            has_preflight_errors=True,
            has_sync_failures=False,
        )
    else:
        cb = callbacks or SyncCallbacks()
        results = run_all_syncs(
            config,
            preflight.sync_statuses,
            options,
            on_rsync_output=cb.on_rsync_output,
            on_sync_start=cb.on_sync_start,
            on_sync_end=cb.on_sync_end,
        )
        return PipelineResult(
            preflight=preflight,
            results=results,
            has_preflight_errors=False,
            has_sync_failures=any(sync_failed(r, strictness) for r in results),
        )


def fatal_sync_errors(
    sync_statuses: dict[str, SyncStatus], strictness: Strictness
) -> dict[str, list[SyncErrorKind]]:
    """Errors of the syncs that made preflight abort under *strictness*."""
    return {
        slug: sorted(s.errors)
        for slug, s in sync_statuses.items()
        if is_fatal(s, strictness)
    }


def run_failed(pipeline: PipelineResult, umount_results: list[UmountResult]) -> bool:
    """Whether a run exits non-zero: preflight aborted it, a sync failed
    under its strictness, or a volume could not be unmounted or locked
    afterwards (a drive possibly left mounted or unlocked)."""
    return (
        pipeline.has_preflight_errors
        or pipeline.has_sync_failures
        or any_failed(umount_results)
    )
