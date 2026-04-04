"""CLI disks mount/umount/status commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from ..config import Config, LocalVolume, RemoteVolume
from ..config.epresolution import NetworkType, ResolvedEndpoints
from ..credentials import build_passphrase_fn
from ..disks.lifecycle import (
    MountResult,
    UmountResult,
    mount_volume_count,
    mount_volumes,
    umount_volumes,
)
from ..disks.observation import build_mount_observations
from ..disks.output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
    volume_display_name,
)
from ..preflight import check_mount_status
from .app import disks_app
from .common import (
    CheckProgressBar,
    OutputFormat,
    VolumeProgressBar,
    load_config_or_exit,
    resolve_endpoints,
)


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


@disks_app.command("mount")
def volumes_mount(
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to config file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    name: Annotated[
        Optional[list[str]],
        typer.Option("--name", "-n", help="Volume name(s) to mount"),
    ] = None,
    location: Annotated[
        Optional[list[str]],
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        Optional[NetworkType],
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
) -> None:
    """Attach LUKS and mount volumes. Mounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )

    use_progress = output == OutputFormat.HUMAN
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }
    total = mount_volume_count(cfg, name)
    mount_bar = (
        VolumeProgressBar(total, "Mounting", _format_mount_result)
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

    try:
        strategies, results = mount_volumes(
            cfg,
            resolved,
            passphrase_fn,
            names=name,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
        )
    finally:
        if mount_bar is not None:
            mount_bar.stop()
        cache.clear()

    observations = build_mount_observations(results, strategies, cfg)
    statuses: list[tuple[str, MountStatusData]] = [
        *((display_names.get(slug, slug), obs) for slug, obs in observations.items()),
        *(
            (display_names.get(r.volume_slug, r.volume_slug), _error_status(r.detail))
            for r in results
            if not r.success and r.volume_slug not in observations
        ),
        *_unmanaged_statuses(cfg, name),
    ]
    _show_status_table(statuses, output)

    if any(not r.success for r in results):
        raise typer.Exit(1)


@disks_app.command("umount")
def volumes_umount(
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to config file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    name: Annotated[
        Optional[list[str]],
        typer.Option("--name", "-n", help="Volume name(s) to umount"),
    ] = None,
    location: Annotated[
        Optional[list[str]],
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        Optional[NetworkType],
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
) -> None:
    """Umount volumes and close LUKS. Umounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    use_progress = output == OutputFormat.HUMAN
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }
    total = mount_volume_count(cfg, name)
    umount_bar = (
        VolumeProgressBar(total, "Umounting", _format_umount_result)
        if use_progress
        else None
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

    results = umount_volumes(
        cfg,
        resolved,
        names=name,
        on_umount_start=on_umount_start,
        on_umount_end=on_umount_end,
    )
    if umount_bar is not None:
        umount_bar.stop()

    _probe_and_show_status(cfg, resolved, output, name)

    if any(not r.success for r in results):
        raise typer.Exit(1)


@dataclass(frozen=True)
class _ErrorStatus:
    """Synthetic mount status for unreachable volumes."""

    resolved_backend: str | None
    device_present: bool | None = None
    luks_attached: bool | None = None
    mounted: bool | None = None


@disks_app.command("status")
def volumes_status(
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to config file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    name: Annotated[
        Optional[list[str]],
        typer.Option("--name", "-n", help="Volume name(s) to check"),
    ] = None,
    location: Annotated[
        Optional[list[str]],
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        Optional[NetworkType],
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
) -> None:
    """Show mount status for volumes with mount config."""
    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    managed = [
        (slug, vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (name is None or slug in name)
    ]

    bar = (
        CheckProgressBar(len(managed))
        if output == OutputFormat.HUMAN and managed
        else None
    )
    managed_statuses = [
        _probe_volume_status(vol, resolved, bar) for _slug, vol in managed
    ]
    if bar is not None:
        bar.stop()

    _show_status_table(
        [*managed_statuses, *_unmanaged_statuses(cfg, name)],
        output,
    )
