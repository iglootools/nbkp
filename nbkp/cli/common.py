"""Shared CLI helpers: config loading, endpoint resolution, pre-flight checks."""

from __future__ import annotations

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..config import (
    Config,
    ConfigError,
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
    load_config,
    resolve_all_endpoints,
)
from ..output import (
    OutputFormat,
    print_config_error,
    print_human_check,
)
from ..preflight import (
    SyncError,
    SyncStatus,
    VolumeStatus,
    check_all_syncs,
)

_INACTIVE_ERRORS = {
    SyncError.SOURCE_SENTINEL_NOT_FOUND,
    SyncError.DESTINATION_SENTINEL_NOT_FOUND,
    SyncError.SOURCE_UNAVAILABLE,
    SyncError.DESTINATION_UNAVAILABLE,
    SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING,
}


def load_config_or_exit(
    config_path: str | None,
) -> Config:
    """Load config or exit with code 2 on error."""
    try:
        return load_config(config_path)
    except ConfigError as e:
        print_config_error(e)
        raise typer.Exit(2)


def build_endpoint_filter(
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> EndpointFilter | None:
    """Build an EndpointFilter from CLI options."""
    locs = locations or []
    excl = exclude_locations or []
    return (
        EndpointFilter(locations=locs, exclude_locations=excl, network=network)
        if locs or excl or network is not None
        else None
    )


def resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> ResolvedEndpoints:
    """Build filter and resolve all endpoints once."""
    ef = build_endpoint_filter(locations, exclude_locations, network)
    return resolve_all_endpoints(cfg, ef)


def _check_total(cfg: Config, only_syncs: list[str] | None) -> int:
    """Count progress steps: volumes + sync endpoints (I/O phases only)."""
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
    return len(volumes) + len(src_eps) + len(dst_eps)


def check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Run check_all_syncs with an optional progress bar."""
    total = _check_total(cfg, only_syncs)
    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
        )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("{task.completed}/{task.total}"),
            transient=True,
        ) as progress:
            task = progress.add_task(
                "Checking volumes, endpoints, and syncs...", total=total
            )

            def on_progress(_slug: str) -> None:
                progress.advance(task)

            return check_all_syncs(
                cfg,
                on_progress=on_progress,
                only_syncs=only_syncs,
                resolved_endpoints=resolved_endpoints,
                dry_run=dry_run,
            )


def check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strict: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
) -> tuple[
    dict[str, VolumeStatus],
    dict[str, SyncStatus],
    bool,
]:
    """Compute statuses, display human output, and check for errors.

    Returns volume statuses, sync statuses, and whether there are
    fatal errors.  When *only_syncs* is given, only those syncs
    (and the volumes they reference) are checked.
    """
    vol_statuses, sync_statuses = check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            vol_statuses,
            sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
        )

    has_errors = (
        any(not s.active for s in sync_statuses.values())
        if strict
        else any(set(s.errors) - _INACTIVE_ERRORS for s in sync_statuses.values())
    )

    return vol_statuses, sync_statuses, has_errors
