"""CLI volumes mount/umount commands."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer
from rich.console import Console

from ..config import NetworkType
from ..credentials import build_passphrase_fn
from ..mount.detection import resolve_mount_strategy
from ..mount.lifecycle import (
    MountResult,
    UmountResult,
    mount_volumes,
    umount_volumes,
)
from ..output import OutputFormat
from .app import volumes_app
from .common import load_config_or_exit, resolve_endpoints


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
