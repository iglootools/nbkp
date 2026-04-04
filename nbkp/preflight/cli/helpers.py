"""Preflight-specific CLI helpers."""

from __future__ import annotations

from ...clihelpers import OutputFormat
from ...clihelpers import StepProgressBar
from ...config import Config, LocalVolume
from ...config.epresolution import ResolvedEndpoints
from ...disks.observation import MountObservation
from ...preflight import PreflightResult, check_all_syncs
from ...preflight.output import print_human_check
from ...run.pipeline import Strictness, has_fatal_errors


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
) -> PreflightResult:
    """Run check_all_syncs with an optional progress bar."""
    total = _check_total(cfg, only_syncs)

    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )

    bar = StepProgressBar(total)
    try:
        return check_all_syncs(
            cfg,
            on_check_start=bar.on_start,
            on_check_end=bar.on_end,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )
    finally:
        bar.stop()


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
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
        )

    return preflight, has_fatal_errors(preflight.sync_statuses, strictness=strictness)
