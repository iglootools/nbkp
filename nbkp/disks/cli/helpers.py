"""Domain-specific disk CLI helpers."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

import typer
from rich.console import Console

from ...clihelpers import OutputFormat
from ...clihelpers import StepProgressBar
from .progress import DisksProgressBar
from ...config import Config, LocalVolume, RemoteVolume
from ...config.epresolution import ResolvedEndpoints
from ...credentials import build_passphrase_fn
from ..context import managed_mount as _disks_managed_mount
from ..lifecycle import MountResult, UmountResult, mount_volume_count
from ..observation import MountObservation
from ..output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
    volume_display_name,
)
from ..strategy import MountStrategy
from ...preflight import check_mount_status


@dataclass(frozen=True)
class _ErrorStatus:
    """Synthetic mount status for unreachable volumes."""

    resolved_backend: str | None
    device_present: bool | None = None
    luks_attached: bool | None = None
    mounted: bool | None = None


def _unmanaged_statuses(
    cfg: Config,
    names: list[str] | None = None,
) -> list[tuple[str, MountStatusData]]:
    """Build status entries for volumes without mount config."""
    return [
        (volume_display_name(vol), _ErrorStatus(resolved_backend="not managed"))
        for slug, vol in cfg.volumes.items()
        if vol.mount is None and (names is None or slug in names)
    ]


def _error_status(detail: str | None) -> _ErrorStatus:
    """Build an error status with red styling for the strategy column."""
    return _ErrorStatus(
        resolved_backend=f"[red]\u2717 {detail}[/red]" if detail else None
    )


def _probe_volume_status(
    vol: LocalVolume | RemoteVolume,
    resolved: ResolvedEndpoints,
    bar: StepProgressBar | None,
) -> tuple[str, MountStatusData]:
    """Probe a single volume's mount status with progress bar updates."""
    assert vol.mount is not None
    label = volume_display_name(vol)
    if bar is not None:
        bar.on_start(label)
    try:
        status: MountStatusData = check_mount_status(vol, vol.mount, resolved)
        if bar is not None:
            bar.on_end(label, True)
        return label, status
    except Exception as e:
        if bar is not None:
            bar.on_end(label, False, str(e))
        return label, _error_status(f"unreachable: {e}")


def _show_status_table(
    statuses: list[tuple[str, MountStatusData]],
    output_format: OutputFormat,
) -> None:
    """Display the mount status table or JSON."""
    match output_format:
        case OutputFormat.JSON:
            typer.echo(json.dumps(build_mount_status_json(statuses), indent=2))
        case OutputFormat.HUMAN:
            if statuses:
                Console().print(build_mount_status_table(statuses))
            else:
                typer.echo("No volumes found.")


def _probe_and_show_status(
    cfg: Config,
    resolved: ResolvedEndpoints,
    output_format: OutputFormat,
    names: list[str] | None = None,
) -> None:
    """Probe mount status and display as table or JSON."""
    managed = [
        (slug, vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (names is None or slug in names)
    ]

    bar = (
        StepProgressBar(len(managed))
        if output_format is OutputFormat.HUMAN and managed
        else None
    )
    statuses: list[tuple[str, MountStatusData]] = [
        *[_probe_volume_status(vol, resolved, bar) for _slug, vol in managed],
        *_unmanaged_statuses(cfg, names),
    ]
    if bar is not None:
        bar.stop()

    _show_status_table(statuses, output_format)


def _format_mount_result(
    slug: str, success: bool, detail: str | None, _warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    return f"{icon} {slug}{detail_str}"


def _format_umount_result(
    slug: str, success: bool, detail: str | None, warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    warning_str = f" [yellow]warning: {warning}[/yellow]" if warning else ""
    return f"{icon} {slug}{detail_str}{warning_str}"


# ── managed_mount context manager ────────────────────────────


def _managed_format_mount_result(
    slug: str, success: bool, detail: str | None, _warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    return f"{icon} mount {slug}{detail_str}"


def _managed_format_umount_result(
    slug: str, success: bool, detail: str | None, warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    warning_str = f" [yellow]warning: {warning}[/yellow]" if warning else ""
    return f"{icon} umount {slug}{detail_str}{warning_str}"


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

    Thin wrapper around :func:`disks.context.managed_mount` that adds
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
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }

    mount_bar = (
        DisksProgressBar(total, "Mounting", _managed_format_mount_result)
        if use_progress
        else None
    )
    umount_bar = (
        DisksProgressBar(total, "Umounting", _managed_format_umount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(display_names.get(slug, slug))

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(
                display_names.get(slug, slug), result.success, result.detail
            )

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(display_names.get(slug, slug))

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(
                display_names.get(slug, slug),
                result.success,
                result.detail,
                result.warning,
            )

    try:
        with _disks_managed_mount(
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
                display_statuses = [
                    (display_names.get(slug, slug), obs)
                    for slug, obs in mount_observations.items()
                ]
                Console().print(build_mount_status_table(display_statuses))
            yield result
    finally:
        if umount_bar is not None:
            umount_bar.stop()
        cache.clear()
