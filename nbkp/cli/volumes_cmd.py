"""CLI volumes mount/umount/status commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Optional

import typer
from rich.console import Console

from ..config.epresolution import NetworkType
from ..credentials import build_passphrase_fn
from ..mount.lifecycle import (
    MountResult,
    UmountResult,
    mount_volume_count,
    mount_volumes,
    umount_volumes,
)
from ..mount.output import (
    MountStatusData,
    build_mount_status_json,
    build_mount_status_table,
)
from ..preflight import check_mount_status
from .app import volumes_app
from .common import (
    OutputFormat,
    VolumeProgressBar,
    load_config_or_exit,
    resolve_endpoints,
)


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


@volumes_app.command("mount")
def volumes_mount(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
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
    total = mount_volume_count(cfg, name)
    mount_bar = (
        VolumeProgressBar(total, "Mounting", _format_mount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(slug)

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(slug, result.success, result.detail)

    try:
        _, results = mount_volumes(
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

    match output:
        case OutputFormat.JSON:
            data = [
                {
                    "volume": r.volume_slug,
                    "success": r.success,
                    "detail": r.detail,
                }
                for r in results
            ]
            typer.echo(json.dumps(data, indent=2))
        case OutputFormat.HUMAN:
            pass  # Already printed via callbacks

    if any(not r.success for r in results):
        raise typer.Exit(1)


@volumes_app.command("umount")
def volumes_umount(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
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
    total = mount_volume_count(cfg, name)
    umount_bar = (
        VolumeProgressBar(total, "Umounting", _format_umount_result)
        if use_progress
        else None
    )

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(slug)

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(slug, result.success, result.detail, result.warning)

    results = umount_volumes(
        cfg,
        resolved,
        names=name,
        on_umount_start=on_umount_start,
        on_umount_end=on_umount_end,
    )
    if umount_bar is not None:
        umount_bar.stop()

    match output:
        case OutputFormat.JSON:
            data = [
                {
                    "volume": r.volume_slug,
                    "success": r.success,
                    "detail": r.detail,
                    "warning": r.warning,
                }
                for r in results
            ]
            typer.echo(json.dumps(data, indent=2))
        case OutputFormat.HUMAN:
            pass  # Already printed via callbacks

    if any(not r.success for r in results):
        raise typer.Exit(1)


@dataclass(frozen=True)
class _ErrorStatus:
    """Synthetic mount status for unreachable volumes."""

    resolved_backend: str | None
    device_present: bool | None = None
    luks_attached: bool | None = None
    mounted: bool | None = None


@volumes_app.command("status")
def volumes_status(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
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

    volumes_with_mount = [
        (slug, vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (name is None or slug in name)
    ]

    if not volumes_with_mount:
        if output == OutputFormat.HUMAN:
            typer.echo("No volumes with mount config found.")
        else:
            typer.echo(json.dumps([]))
        return

    console = Console()
    statuses: list[tuple[str, MountStatusData]] = []

    for slug, vol in volumes_with_mount:
        assert vol.mount is not None
        status_display = None
        if output == OutputFormat.HUMAN:
            status_display = console.status(f"Checking {slug}...")
            status_display.start()
        try:
            statuses.append((slug, check_mount_status(vol, vol.mount, resolved)))
        except Exception as e:
            statuses.append((slug, _ErrorStatus(resolved_backend=f"unreachable: {e}")))
        finally:
            if status_display is not None:
                status_display.stop()

    match output:
        case OutputFormat.JSON:
            typer.echo(json.dumps(build_mount_status_json(statuses), indent=2))
        case OutputFormat.HUMAN:
            console.print(build_mount_status_table(statuses))
