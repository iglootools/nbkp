"""Shared CLI helpers: config loading, endpoint resolution, pre-flight checks."""

from __future__ import annotations

import enum
from contextlib import contextmanager
from typing import Callable, Generator

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..config import (
    Config,
    ConfigError,
    load_config,
)
from ..config.epresolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
)
from ..remote.resolution import resolve_all_endpoints
from ..credentials import build_passphrase_fn
from ..mount.lifecycle import MountResult, UmountResult
from ..mount.observation import MountObservation
from ..mount.strategy import MountStrategy
from ..orchestration import managed_mount as _orchestration_managed_mount
from ..config.output import print_config_error
from ..preflight.output import print_human_check
from ..preflight import (
    SyncStatus,
    VolumeError,
    VolumeStatus,
    check_all_syncs,
)
from ..sync.pipeline import has_fatal_errors


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"


_INACTIVE_VOLUME_ERRORS = {
    VolumeError.DEVICE_NOT_PRESENT,
    VolumeError.VOLUME_NOT_MOUNTED,
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


def _validate_locations(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
) -> None:
    """Exit with an error if any location value is not defined in the config."""
    known = set(cfg.known_locations())
    if not known:
        all_values = [*(locations or []), *(exclude_locations or [])]
        if all_values:
            typer.echo(
                "Error: no locations are defined in the configuration."
                " --location and --exclude-location cannot be used.",
                err=True,
            )
            raise typer.Exit(2)
        return
    for label, values in [
        ("--location", locations),
        ("--exclude-location", exclude_locations),
    ]:
        for v in values or []:
            if v not in known:
                typer.echo(
                    f"Error: unknown location '{v}' passed to {label}."
                    f" Known locations: {', '.join(sorted(known))}",
                    err=True,
                )
                raise typer.Exit(2)


def resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> ResolvedEndpoints:
    """Build filter and resolve all endpoints once."""
    _validate_locations(cfg, locations, exclude_locations)
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
    mount_observations: dict[str, MountObservation] | None = None,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Run check_all_syncs with an optional progress bar."""
    from ..preflight import PreflightResult

    total = _check_total(cfg, only_syncs)

    def _run(
        on_progress: Callable[[str], None] | None = None,
    ) -> PreflightResult:
        return check_all_syncs(
            cfg,
            on_progress=on_progress,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )

    if not use_progress or total == 0:
        result = _run()
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

            result = _run(on_progress)

    return result.volume_statuses, result.sync_statuses


def check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strict: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
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
        mount_observations=mount_observations,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            vol_statuses,
            sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
        )

    return vol_statuses, sync_statuses, has_fatal_errors(sync_statuses, strict=strict)


@contextmanager
def managed_mount(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    mount: bool = True,
    umount: bool = True,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> Generator[
    tuple[dict[str, MountStrategy], dict[str, MountObservation]],
    None,
    None,
]:
    """Context manager that mounts volumes on entry and umounts on exit.

    Thin wrapper around :func:`orchestration.managed_mount` that adds
    Rich display callbacks and credential management.

    Yields a tuple of ``(mount_strategy, mount_observations)``.  When
    mounting is skipped both dicts are empty.  Observations capture
    the runtime state discovered during mount so that preflight checks
    can reuse it instead of re-probing.

    Parameters
    ----------
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    output_format:
        Controls whether Rich spinner / result lines are printed.
    """
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )

    # ── Rich mount callbacks ───────────────────────────────────
    console_mount = Console()
    mount_status = None

    def on_mount_start(slug: str) -> None:
        nonlocal mount_status
        if output_format is OutputFormat.HUMAN:
            mount_status = console_mount.status(f"Mounting {slug}...")
            mount_status.start()

    def on_mount_end(slug: str, result: MountResult) -> None:
        nonlocal mount_status
        if mount_status is not None:
            mount_status.stop()
            mount_status = None
        if output_format is OutputFormat.HUMAN:
            icon = "[green]\u2713[/green]" if result.success else "[red]\u2717[/red]"
            detail = f" ({result.detail})" if result.detail else ""
            console_mount.print(f"{icon} mount {slug}{detail}")

    # ── Rich umount callbacks ──────────────────────────────────
    umount_console = Console()
    umount_status = None

    def on_umount_start(slug: str) -> None:
        nonlocal umount_status
        if output_format is OutputFormat.HUMAN:
            umount_status = umount_console.status(f"Umounting {slug}...")
            umount_status.start()

    def on_umount_end(slug: str, result: UmountResult) -> None:
        nonlocal umount_status
        if umount_status is not None:
            umount_status.stop()
            umount_status = None
        if output_format is OutputFormat.HUMAN:
            icon = "[green]\u2713[/green]" if result.success else "[red]\u2717[/red]"
            detail = f" ({result.detail})" if result.detail else ""
            warning = (
                f" [yellow]warning: {result.warning}[/yellow]" if result.warning else ""
            )
            umount_console.print(f"{icon} umount {slug}{detail}{warning}")

    try:
        with _orchestration_managed_mount(
            cfg,
            resolved,
            passphrase_fn,
            mount=mount,
            umount=umount,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
            on_umount_start=on_umount_start,
            on_umount_end=on_umount_end,
        ) as result:
            yield result
    finally:
        cache.clear()
