"""CLI volumes mount/umount/status commands."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from ..config.epresolution import NetworkType
from ..credentials import build_passphrase_fn
from ..mount.detection import resolve_mount_strategy
from ..mount.lifecycle import (
    MountResult,
    UmountResult,
    mount_volumes,
    umount_volumes,
)
from ..preflight import check_mount_status
from .app import volumes_app
from .common import OutputFormat, load_config_or_exit, resolve_endpoints


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

    mount_strategy = resolve_mount_strategy(cfg, resolved, name)

    console = Console()
    status_display = None

    def on_mount_start(slug: str) -> None:
        nonlocal status_display
        if output == OutputFormat.HUMAN:
            status_display = console.status(f"Mounting {slug}...")
            status_display.start()

    def on_mount_end(slug: str, result: MountResult) -> None:
        nonlocal status_display
        if status_display is not None:
            status_display.stop()
            status_display = None
        if output == OutputFormat.HUMAN:
            icon = "[green]\u2713[/green]" if result.success else "[red]\u2717[/red]"
            detail = f" ({result.detail})" if result.detail else ""
            console.print(f"{icon} {slug}{detail}")

    try:
        results = mount_volumes(
            cfg,
            resolved,
            passphrase_fn,
            names=name,
            mount_strategy=mount_strategy,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
        )
    finally:
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

    mount_strategy = resolve_mount_strategy(cfg, resolved, name)

    console = Console()
    status_display = None

    def on_umount_start(slug: str) -> None:
        nonlocal status_display
        if output == OutputFormat.HUMAN:
            status_display = console.status(f"Umounting {slug}...")
            status_display.start()

    def on_umount_end(slug: str, result: UmountResult) -> None:
        nonlocal status_display
        if status_display is not None:
            status_display.stop()
            status_display = None
        if output == OutputFormat.HUMAN:
            icon = "[green]\u2713[/green]" if result.success else "[red]\u2717[/red]"
            detail = f" ({result.detail})" if result.detail else ""
            warning = (
                f" [yellow]warning: {result.warning}[/yellow]" if result.warning else ""
            )
            console.print(f"{icon} {slug}{detail}{warning}")

    results = umount_volumes(
        cfg,
        resolved,
        names=name,
        mount_strategy=mount_strategy,
        on_umount_start=on_umount_start,
        on_umount_end=on_umount_end,
    )

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


def _mount_state_icon(value: bool | None) -> str:
    """Format a mount state value as ✓, ✗, or —."""
    match value:
        case True:
            return "[green]\u2713[/green]"
        case False:
            return "[red]\u2717[/red]"
        case None:
            return "\u2014"


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
    results: list[tuple[str, str | None, bool | None, bool | None, bool | None]] = []

    for slug, vol in volumes_with_mount:
        assert vol.mount is not None
        status_display = None
        if output == OutputFormat.HUMAN:
            status_display = console.status(f"Checking {slug}...")
            status_display.start()
        caps = check_mount_status(vol, vol.mount, resolved)
        if status_display is not None:
            status_display.stop()
        results.append(
            (
                slug,
                caps.resolved_backend,
                caps.device_present,
                caps.luks_attached,
                caps.mounted,
            )
        )

    match output:
        case OutputFormat.JSON:
            data = [
                {
                    "volume": slug,
                    "strategy": strategy,
                    "device_present": device,
                    "luks_attached": luks,
                    "mounted": mounted,
                }
                for slug, strategy, device, luks, mounted in results
            ]
            typer.echo(json.dumps(data, indent=2))
        case OutputFormat.HUMAN:
            table = Table(title="Volume Mount Status:")
            table.add_column("Name", style="bold")
            table.add_column("Strategy")
            table.add_column("Device")
            table.add_column("LUKS")
            table.add_column("Mounted")
            for slug, strategy, device, luks, mounted in results:
                table.add_row(
                    slug,
                    strategy or "?",
                    _mount_state_icon(device),
                    _mount_state_icon(luks),
                    _mount_state_icon(mounted),
                )
            console.print(table)
