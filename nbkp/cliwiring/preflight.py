"""Preflight checks with a progress bar, shared by every command that runs them.

``preflight check|troubleshoot``, ``snapshots show|prune`` and ``run`` all
probe the config before acting on it; they share the step counting and the
per-step severity icons here.
"""

from collections.abc import Sequence

from ..clidisplay import StepProgressBar
from ..config import Config
from ..disks import MountObservation
from ..policy import Strictness
from ..preflight import PreflightResult, check_all_syncs, check_step_count
from ..preflight.severity import PreflightError, severity_for_errors
from ..remote.endpoints import ResolvedEndpoints


def check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> PreflightResult:
    """*strictness* picks the per-step icon: errors that are fatal under
    the current policy show ``✗`` (red), errors that are non-fatal
    (e.g. inactive volumes under ``IGNORE_INACTIVE``) show ``⚠``
    (orange).
    """
    total = check_step_count(cfg, only_syncs, resolved_endpoints)

    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )

    with StepProgressBar(total) as bar:

        def _on_start(label: str) -> None:
            bar.on_start(f"Checking {label}...")

        def _on_end(label: str, errors: Sequence[PreflightError]) -> None:
            typed_errors = list(errors)
            severity = severity_for_errors(typed_errors, strictness)
            summary = ", ".join(e.value for e in typed_errors) if typed_errors else None
            bar.on_end(f"check {label}", severity, summary)

        return check_all_syncs(
            cfg,
            on_check_start=_on_start,
            on_check_end=_on_end,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )
