"""Preflight-specific CLI helpers."""

from __future__ import annotations

from collections.abc import Sequence

from ...clihelpers import OutputFormat, StepProgressBar
from ...config import Config, LocalVolume
from ...config.epresolution import ResolvedEndpoints
from ...disks.observation import MountObservation
from ...preflight import PreflightResult, check_all_syncs
from ...preflight.output import print_human_check
from ..severity import PreflightError, severity_for_errors
from ..strictness import Strictness, has_fatal_errors


def _check_total(cfg: Config, only_syncs: list[str] | None) -> int:
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
    total = _check_total(cfg, only_syncs)

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

        def _on_end(label: str, errors: Sequence[object]) -> None:
            # The checks-layer callback passes any-enum errors; the
            # severity helper handles them uniformly via duck-typing
            # on _is_inactive's match-case.
            typed_errors: list[PreflightError] = list(errors)  # type: ignore[arg-type]
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


def check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strictness: Strictness,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
) -> tuple[PreflightResult, bool]:
    """Compute statuses, display human output, and check for errors.

    Returns the preflight result and whether there are fatal errors.
    When *only_syncs* is given, only those syncs (and the volumes
    they reference) are checked.
    """
    preflight = check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
        strictness=strictness,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
            strictness=strictness,
        )

    return preflight, has_fatal_errors(preflight.sync_statuses, strictness=strictness)
