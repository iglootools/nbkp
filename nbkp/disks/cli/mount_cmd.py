"""Disks mount command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from ...clihelpers import OutputFormat
from ...commands.config import load_config_or_exit, resolve_endpoints
from ...commands.invocation import Invocation
from ...commands.mount_progress import LifecycleProgress, mount_display_names
from ...config import Config
from ...config.epresolution import NetworkType, ResolvedEndpoints
from ...credentials import build_passphrase_fn, prefetch_passphrases
from ..lifecycle import MountResult, mount_volumes
from ..models import MountFailureReason
from ..observation import build_mount_observations
from ..plan import plan_lifecycle
from . import app
from .helpers import (
    _error_label,
    _ErrorStatus,
    _show_status_table,
    _unmanaged_statuses,
    require_known_names,
)
from .helpers.plan_output import show_plan


@app.command("mount")
def mount(
    config: Annotated[
        Path | None,
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
        list[str] | None,
        typer.Option("--name", "-n", help="Volume name(s) to mount"),
    ] = None,
    location: Annotated[
        list[str] | None,
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
    ] = None,
    exclude_location: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        NetworkType | None,
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Report what would be unlocked and mounted without changing"
                " anything (no passphrase retrieval, no udisksctl unlock/mount)."
                " Long form only: -n is --name here."
            ),
        ),
    ] = False,
) -> None:
    """Unlock LUKS and mount volumes. Mounts all volumes with mount config, or specific ones via --name."""
    cfg = load_config_or_exit(config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)

    if dry_run:
        show_plan(
            plan_lifecycle(cfg, resolved, mounting=True, names=name),
            mount_display_names(cfg),
            output,
        )
        return

    results = _mount_with_progress(cfg, resolved, name, output)
    _show_results(cfg, results, name, output)
    if output is OutputFormat.HUMAN:
        _print_auth_hint(
            results,
            Invocation.of(config, network=network.value if network else None),
        )
    if any(not r.success for r in results):
        raise typer.Exit(1)


def _mount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
) -> list[MountResult]:
    """Prefetch every passphrase, then mount, with progress bars for humans."""
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )
    progress = LifecycleProgress.create(
        cfg, enabled=output is OutputFormat.HUMAN, names=names, umounting=False
    )
    # try/finally instead of `with` because the bars are conditionally
    # created (None when output is JSON), and cache.clear() must also run.
    try:
        # Retrieve every configured passphrase before touching a device —
        # deliberately including the ones --name excludes and the ones whose
        # drive is absent, so one approval pass covers every drive.
        prefetch_passphrases(
            cfg,
            passphrase_fn,
            on_prefetch_start=progress.on_prefetch_start,
            on_prefetch_end=progress.on_prefetch_end,
        )
        return mount_volumes(
            cfg,
            resolved,
            passphrase_fn,
            names=names,
            on_mount_start=progress.on_mount_start,
            on_mount_end=progress.on_mount_end,
        )
    finally:
        progress.stop_all()
        cache.clear()


def _show_results(
    cfg: Config,
    results: list[MountResult],
    names: list[str] | None,
    output: OutputFormat,
) -> None:
    """Status table: observed volumes, unreachable ones, unmanaged ones."""
    display_names = mount_display_names(cfg)
    observations = build_mount_observations(results)
    _show_status_table(
        [
            *(
                (display_names.get(slug, slug), obs)
                for slug, obs in observations.items()
            ),
            *(
                (
                    _error_label(
                        display_names.get(r.volume_slug, r.volume_slug), r.detail
                    ),
                    _ErrorStatus(),
                )
                for r in results
                if not r.success and r.volume_slug not in observations
            ),
            *_unmanaged_statuses(cfg, names),
        ],
        output,
    )


def _print_auth_hint(results: list[MountResult], invocation: Invocation) -> None:
    """Point at ``disks setup-auth`` when polkit refused an unlock or mount."""
    if any(r.failure_reason is MountFailureReason.NOT_AUTHORIZED for r in results):
        console = Console()
        console.print(
            "udisks refused the operation (no polkit rule). Generate one with:"
        )
        # soft_wrap: the command must stay on one line to copy-paste.
        console.print(
            Text(
                invocation.command("disks", "setup-auth", endpoint_flags=False),
                style="bold",
            ),
            soft_wrap=True,
        )
