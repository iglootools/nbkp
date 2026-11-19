"""Preflight checks with a progress bar, shared by every command that runs them.

``preflight check|troubleshoot``, ``snapshots show|prune`` and ``run`` all
probe the config before acting on it; they share the step counting and the
per-step severity icons here.
"""

from collections.abc import Collection, Sequence
from functools import partial

from ..clidisplay import StepProgressBar
from ..config import Config
from ..disks import MountObservation
from ..policy import Strictness
from ..preflight import (
    PreflightErrorKind,
    PreflightResult,
    check_all_syncs,
    check_step_count,
    format_problems,
    severity_for_errors,
)
from ..remote.endpoints import ResolvedEndpoints
from .runtime import Runtime


def start_check_step(bar: StepProgressBar, label: str) -> None:
    bar.on_start(f"Checking {label}...")


def end_check_step(
    bar: StepProgressBar,
    label: str,
    errors: Sequence[PreflightErrorKind],
    strictness: Strictness,
) -> None:
    """Close a step with the icon its errors deserve under *strictness*."""
    typed_errors = list(errors)
    severity = severity_for_errors(typed_errors, strictness)
    summary = format_problems(typed_errors) if typed_errors else None
    bar.on_end(f"check {label}", severity, summary)


def check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    exclude_locations: Collection[str] = (),
    *,
    runtime: Runtime,
) -> PreflightResult:
    """*strictness* picks the per-step icon: errors that are fatal under
    the current policy show ``✗`` (red), errors that are non-fatal
    (e.g. inactive volumes under ``IGNORE_INACTIVE``) show ``⚠``
    (orange).  The probes run through *runtime*.
    """
    total = check_step_count(cfg, only_syncs, resolved_endpoints, exclude_locations)

    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
            exclude_locations=exclude_locations,
            run=runtime.run,
            which=runtime.which,
        )

    with StepProgressBar(total) as bar:
        return check_all_syncs(
            cfg,
            on_check_start=partial(start_check_step, bar),
            on_check_end=lambda label, errors: end_check_step(
                bar, label, errors, strictness
            ),
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
            exclude_locations=exclude_locations,
            run=runtime.run,
            which=runtime.which,
        )
