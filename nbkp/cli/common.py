"""Shared CLI helpers: config loading, endpoint resolution, pre-flight checks."""

from __future__ import annotations

import enum
from contextlib import contextmanager
from typing import Callable, Generator

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)

from ..config import (
    Config,
    ConfigError,
    LocalVolume,
    load_config,
)
from ..config.epresolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
)
from ..remote.resolution import resolve_all_endpoints
from ..credentials import build_passphrase_fn
from ..mount.lifecycle import MountResult, UmountResult, mount_volume_count
from ..mount.observation import MountObservation
from ..mount.output import build_mount_status_table
from ..mount.strategy import MountStrategy
from ..orchestration import managed_mount as _orchestration_managed_mount
from ..config.output import print_config_error
from ..preflight.output import print_human_check
from ..preflight import (
    PreflightResult,
    check_all_syncs,
)
from ..sync.pipeline import Strictness, has_fatal_errors


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"


def _format_mount_result(
    slug: str, success: bool, detail: str | None, _warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    return f"{icon} mount {slug}{detail_str}"


def _format_umount_result(
    slug: str, success: bool, detail: str | None, warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    warning_str = f" [yellow]warning: {warning}[/yellow]" if warning else ""
    return f"{icon} umount {slug}{detail_str}{warning_str}"


class VolumeProgressBar:
    """Rich progress bar for mount/umount operations.

    Manages a transient progress bar that shows a spinner, description
    (current volume name), visual bar, and M/N counter.  Result lines
    are printed above the bar as each volume completes.

    Parameters
    ----------
    total:
        Number of volumes to process.
    label:
        Verb shown in the progress description (e.g. ``"Mounting"``).
    format_result:
        Callable that formats a result line given ``(slug, success, detail,
        warning)``.  Called once per volume on completion.
    """

    def __init__(
        self,
        total: int,
        label: str,
        format_result: Callable[[str, bool, str | None, str | None], str],
    ) -> None:
        self._total = total
        self._label = label
        self._format_result = format_result
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def on_start(self, slug: str) -> None:
        """Call at the beginning of each volume operation."""
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(
                f"{self._label} {slug}...", total=self._total
            )
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=f"{self._label} {slug}...")

    def on_end(
        self,
        slug: str,
        success: bool,
        detail: str | None = None,
        warning: str | None = None,
    ) -> None:
        """Call at the end of each volume operation."""
        if self._progress is not None:
            assert self._task_id is not None
            line = self._format_result(slug, success, detail, warning)
            self._progress.console.print(line)
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()


class CheckProgressBar:
    """Rich progress bar for preflight checks.

    Shows a spinner, description (current check label), visual bar,
    and M/N counter.  Result lines (✓/✗) are printed above the bar
    as each check completes.

    Parameters
    ----------
    total:
        Number of checks to perform.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def on_start(self, label: str) -> None:
        """Call before each check begins."""
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(
                f"Checking {label}...", total=self._total
            )
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=f"Checking {label}...")

    def on_end(
        self,
        label: str,
        active: bool,
        error_summary: str | None = None,
    ) -> None:
        """Call after each check completes."""
        if self._progress is not None:
            assert self._task_id is not None
            icon = "[green]\u2713[/green]" if active else "[red]\u2717[/red]"
            detail = f" ({error_summary})" if error_summary else ""
            self._progress.console.print(f"{icon} check {label}{detail}")
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()


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

    bar = CheckProgressBar(total)
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

    use_progress = output_format is OutputFormat.HUMAN
    total = mount_volume_count(cfg)

    mount_bar = (
        VolumeProgressBar(total, "Mounting", _format_mount_result)
        if use_progress
        else None
    )
    umount_bar = (
        VolumeProgressBar(total, "Umounting", _format_umount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(slug)

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(slug, result.success, result.detail)

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(slug)

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(slug, result.success, result.detail, result.warning)

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
            if mount_bar is not None:
                mount_bar.stop()
            _mount_strategy, mount_observations = result
            if use_progress and mount_observations:
                Console().print(
                    build_mount_status_table(list(mount_observations.items()))
                )
            yield result
    finally:
        if umount_bar is not None:
            umount_bar.stop()
        cache.clear()
