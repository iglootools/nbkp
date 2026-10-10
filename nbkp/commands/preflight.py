"""Preflight checks with a progress bar, shared by every command that runs them.

``preflight check|troubleshoot``, ``snapshots show|prune`` and ``run`` all
probe the config before acting on it; they share the step counting and the
per-step severity icons here.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..clihelpers import StepProgressBar
from ..config import Config, LocalVolume
from ..disks import MountObservation
from ..policy import Strictness
from ..preflight import PreflightResult, check_all_syncs
from ..preflight.severity import PreflightError, severity_for_errors
from ..remote.endpoints import ResolvedEndpoints


def check_total(cfg: Config, only_syncs: list[str] | None) -> int:
    """Count progress steps: SSH endpoints + volumes + sync endpoints.

    Matches the ``_track()`` calls in ``check_all_syncs``: one per SSH
    endpoint (volume-referenced + all remaining defined endpoints), one
    per volume, and one per source/destination sync endpoint.
    """
    syncs = (
        {s: sc for s, sc in cfg.syncs.items() if s in only_syncs}
        if only_syncs
        else cfg.syncs
    )
    src_eps = {cfg.source_endpoint(sc).slug for sc in syncs.values()}
    dst_eps = {cfg.destination_endpoint(sc).slug for sc in syncs.values()}
    volumes = (
        {cfg.source_endpoint(sc).volume for sc in syncs.values()}
        | {cfg.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(cfg.volumes.keys())
    )

    # SSH endpoints: volume-referenced + all remaining defined endpoints
    volume_ssh_slugs = {
        "localhost"
        if isinstance(cfg.volumes[v_slug], LocalVolume)
        else cfg.volumes[v_slug].ssh_endpoint  # type: ignore[union-attr]
        for v_slug in volumes
    }
    remaining_slugs = set(cfg.ssh_endpoints.keys()) - volume_ssh_slugs
    ssh_count = len(volume_ssh_slugs) + len(remaining_slugs)

    return ssh_count + len(volumes) + len(src_eps) + len(dst_eps)


def check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> PreflightResult:
    """Run check_all_syncs with an optional progress bar.

    *strictness* picks the per-step icon: errors that are fatal under
    the current policy show ``✗`` (red), errors that are non-fatal
    (e.g. inactive volumes under ``IGNORE_INACTIVE``) show ``⚠``
    (orange).
    """
    total = check_total(cfg, only_syncs)

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
