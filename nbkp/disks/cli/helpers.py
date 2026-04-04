"""Domain-specific disk CLI helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass

import typer
from rich.console import Console

from ...clihelpers import CheckProgressBar, OutputFormat
from ...config import Config, LocalVolume, RemoteVolume
from ...config.epresolution import ResolvedEndpoints
from ..output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
    volume_display_name,
)
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
    bar: CheckProgressBar | None,
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
        CheckProgressBar(len(managed))
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
