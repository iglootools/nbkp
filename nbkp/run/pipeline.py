"""Check-then-run pipeline: preflight checks followed by sync execution.

Composes ``check_all_syncs`` and ``run_all_syncs`` into a single
reusable function shared by the CLI ``run`` command and integration
tests.  Display/output, mount lifecycle, and config loading are the
caller's responsibility.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Callable

from ..config import Config
from ..config.epresolution import ResolvedEndpoints
from ..preflight import (
    PreflightResult,
    SyncStatus,
    VolumeStatus,
    check_all_syncs,
)
from ..sync.rsync import ProgressMode
from ..sync.runner import SyncResult, run_all_syncs


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a check-then-run pipeline execution."""

    preflight: PreflightResult
    results: list[SyncResult]
    """Empty when preflight found fatal errors and syncs were not executed."""
    has_preflight_errors: bool
    has_sync_failures: bool
    """True when any sync failed for a reason other than expected inactivity."""

    @property
    def vol_statuses(self) -> dict[str, VolumeStatus]:
        """Backward-compatible access to volume statuses."""
        return self.preflight.volume_statuses

    @property
    def sync_statuses(self) -> dict[str, SyncStatus]:
        """Backward-compatible access to sync statuses."""
        return self.preflight.sync_statuses


def is_expected_skip(
    result: SyncResult,
    sync_statuses: dict[str, SyncStatus],
) -> bool:
    """Return True if a failed sync result is an expected inactive skip.

    Uses ``SyncStatus.is_expected_inactive()`` to check all 4 layers
    of the error model rather than a flat set of sync-level errors.
    """
    ss = sync_statuses.get(result.sync_slug)
    return ss is not None and ss.is_expected_inactive()


class Strictness(str, enum.Enum):
    """Controls how preflight errors affect the exit code.

    - ``IGNORE_NONE``: All errors are fatal — any inactive sync
      (including missing sentinels) aborts the run.
    - ``IGNORE_INACTIVE``: Expected-inactive errors (missing sentinels,
      unreachable hosts) are silently skipped; infrastructure errors
      are still fatal.  This is the default.
    - ``IGNORE_ALL``: All preflight errors are ignored — only sync
      execution failures cause a non-zero exit.
    """

    IGNORE_NONE = "ignore-none"
    IGNORE_INACTIVE = "ignore-inactive"
    IGNORE_ALL = "ignore-all"


def has_fatal_errors(
    sync_statuses: dict[str, SyncStatus],
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> bool:
    """Return True if any sync has errors that should abort the run.

    See :class:`Strictness` for the three modes.
    """
    match strictness:
        case Strictness.IGNORE_NONE:
            return any(not s.active for s in sync_statuses.values())
        case Strictness.IGNORE_INACTIVE:
            return any(
                not s.active and not s.is_expected_inactive()
                for s in sync_statuses.values()
            )
        case Strictness.IGNORE_ALL:
            return False


def check_and_run(
    config: Config,
    *,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_check_start: Callable[[str], None] | None = None,
    on_check_end: Callable[[str, bool, str | None], None] | None = None,
    on_checks_done: Callable[[PreflightResult], None] | None = None,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    mount_observations: dict[str, Any] | None = None,
) -> PipelineResult:
    """Run preflight checks, then execute syncs if no fatal errors.

    This is the core "check → run" pipeline shared by the CLI ``run``
    command and integration tests.  It does **not** include
    display/output logic, mount lifecycle, or config loading — those
    remain the caller's responsibility.

    Parameters
    ----------
    strictness:
        Controls how preflight errors are treated.  See
        :class:`Strictness` for details.
    on_check_start:
        Called before each check with a label (e.g. ``"ssh:localhost"``).
    on_check_end:
        Called after each check with ``(label, active, error_summary)``.
    on_checks_done:
        Called after preflight completes but before syncs start.
        Fires regardless of whether there are fatal errors, so the
        CLI can print the check table in both cases.
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

    preflight_errors = has_fatal_errors(preflight.sync_statuses, strictness=strictness)

    if preflight_errors:
        return PipelineResult(
            preflight=preflight,
            results=[],
            has_preflight_errors=True,
            has_sync_failures=True,
        )

    results = run_all_syncs(
        config,
        preflight.sync_statuses,
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
        not r.success and not is_expected_skip(r, preflight.sync_statuses)
        for r in results
    )

    return PipelineResult(
        preflight=preflight,
        results=results,
        has_preflight_errors=False,
        has_sync_failures=sync_failures,
    )
