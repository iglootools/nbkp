"""Check-then-run pipeline: preflight checks followed by sync execution.

Composes ``check_all_syncs`` and ``run_all_syncs`` into a single
reusable function shared by the CLI ``run`` command and integration
tests.  Display/output, mount lifecycle, and config loading are the
caller's responsibility.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..config import Config
from ..disks.observation import MountObservation
from ..policy import Severity, Strictness
from ..preflight import (
    PreflightResult,
    SyncStatus,
    VolumeStatus,
    check_all_syncs,
)
from ..preflight.severity import PreflightError
from ..preflight.strictness import has_fatal_errors
from ..remote.endpoints import ResolvedEndpoints
from ..sync.rsync import ProgressMode
from ..sync.runner import SyncResult, result_severity, run_all_syncs

__all__ = [
    "PipelineResult",
    "Strictness",
    "SyncCallbacks",
    "check_and_run",
    "sync_failed",
]


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a check-then-run pipeline execution."""

    preflight: PreflightResult
    results: list[SyncResult]
    """Empty when preflight found fatal errors and syncs were not executed."""
    has_preflight_errors: bool
    has_sync_failures: bool
    """True when any result is fatal under the strictness (see ``sync_failed``)."""

    @property
    def vol_statuses(self) -> dict[str, VolumeStatus]:
        """Backward-compatible access to volume statuses."""
        return self.preflight.volume_statuses

    @property
    def sync_statuses(self) -> dict[str, SyncStatus]:
        """Backward-compatible access to sync statuses."""
        return self.preflight.sync_statuses


@dataclass(frozen=True)
class SyncCallbacks:
    """Progress callbacks fired while syncs run."""

    on_rsync_output: Callable[[str], None] | None = None
    on_sync_start: Callable[[str], None] | None = None
    on_sync_end: Callable[[str, SyncResult], None] | None = None


def sync_failed(result: SyncResult, strictness: Strictness) -> bool:
    """Whether *result* makes the run fail under *strictness*.

    Runtime failures, and cancellations caused by one, always do.  Skips
    and cancellations rooted in expected inactivity only do under
    ``ignore-none``; skips for infrastructure errors do unless
    ``ignore-all``.
    """
    return result_severity(result, strictness) is Severity.ERROR


def check_and_run(
    config: Config,
    *,
    clock: Callable[[], datetime],
    platform: str,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_check_start: Callable[[str], None] | None = None,
    on_check_end: Callable[[str, Sequence[PreflightError]], None] | None = None,
    on_checks_done: Callable[[PreflightResult], None] | None = None,
    callbacks: SyncCallbacks | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observations: dict[str, MountObservation] | None = None,
) -> PipelineResult:
    """Run preflight checks, then execute syncs if no fatal errors.

    Parameters
    ----------
    clock, platform:
        Snapshot naming inputs (current UTC time, ``sys.platform``),
        supplied by the entry point.
    strictness:
        Controls how preflight errors are treated.  See
        :class:`Strictness` for details.
    on_check_start / on_check_end:
        Called before / after each check with a label (e.g.
        ``"ssh:localhost"``) and, after, the check's errors.
    on_checks_done:
        Called after preflight completes but before syncs start, whether
        or not there are fatal errors, so the CLI can print the check
        table in both cases.
    """
    preflight = check_all_syncs(
        config,
        on_check_start=on_check_start,
        on_check_end=on_check_end,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
    )
    if on_checks_done is not None:
        on_checks_done(preflight)

    if has_fatal_errors(preflight.sync_statuses, strictness=strictness):
        return PipelineResult(
            preflight=preflight,
            results=[],
            has_preflight_errors=True,
            has_sync_failures=True,
        )
    else:
        cb = callbacks or SyncCallbacks()
        results = run_all_syncs(
            config,
            preflight.sync_statuses,
            clock=clock,
            platform=platform,
            strictness=strictness,
            dry_run=dry_run,
            only_syncs=only_syncs,
            progress=progress,
            prune=prune,
            on_rsync_output=cb.on_rsync_output,
            on_sync_start=cb.on_sync_start,
            on_sync_end=cb.on_sync_end,
            resolved_endpoints=resolved_endpoints,
        )
        return PipelineResult(
            preflight=preflight,
            results=results,
            has_preflight_errors=False,
            has_sync_failures=any(sync_failed(r, strictness) for r in results),
        )
