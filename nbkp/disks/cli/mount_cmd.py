from functools import partial
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from ...clidisplay import OutputFormat
from ...clidisplay.invocation import Invocation, format_troubleshoot_hint
from ...cliwiring.config import load_config_or_exit, resolve_endpoints
from ...cliwiring.disks import require_known_names, show_plan, show_volume_statuses
from ...cliwiring.mount import lifecycle_session
from ...cliwiring.mount_progress import mount_display_names
from ...cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
)
from ...cliwiring.runtime import Runtime, runtime_of
from ...config import Config
from ...remote.endpoints import ResolvedEndpoints
from ..context import mount_phase
from ..lifecycle import MountResult, any_failed, refused_by_polkit
from ..mount_checks import check_mount_status
from ..output import mount_result_json
from ..plan import plan_lifecycle
from ..status import mount_result_statuses, unprobed_volume_statuses
from . import app
from .options import NAME_OPTION


@app.command("mount")
def mount_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    name: NAME_OPTION = None,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
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
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)

    if dry_run:
        show_plan(
            plan_lifecycle(
                cfg,
                resolved,
                mounting=True,
                names=name,
                probe=partial(check_mount_status, run=runtime.run),
            ),
            mount_display_names(cfg),
            output,
        )
        return

    results = _mount_with_progress(cfg, resolved, name, output, runtime)
    _show_results(cfg, resolved, results, name, output)
    invocation = Invocation.of(config, location, exclude_location, network)
    if output is OutputFormat.HUMAN:
        _print_auth_hint(results, invocation)
    if any_failed(results):
        if output is OutputFormat.HUMAN:
            Console(stderr=True).print(
                format_troubleshoot_hint(invocation), soft_wrap=True
            )
        raise typer.Exit(1)


def _mount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
    runtime: Runtime,
) -> list[MountResult]:
    with lifecycle_session(
        cfg,
        resolved,
        runtime=runtime,
        enabled=output is OutputFormat.HUMAN,
        names=names,
        umounting=False,
    ) as (passphrase_fn, progress):
        return mount_phase(
            cfg,
            resolved,
            passphrase_fn=passphrase_fn,
            names=names,
            on_prefetch_start=progress.on_prefetch_start,
            on_prefetch_end=progress.on_prefetch_end,
            on_mount_start=progress.on_mount_start,
            on_mount_end=progress.on_mount_end,
            run=runtime.run,
        )


def _show_results(
    cfg: Config,
    resolved: ResolvedEndpoints,
    results: list[MountResult],
    names: list[str] | None,
    output: OutputFormat,
) -> None:
    """Status table: observed, unreachable, unmanaged and excluded volumes.

    The JSON entries of the volumes acted on also carry why a mount failed,
    not only the state it left.
    """
    show_volume_statuses(
        cfg,
        [
            *mount_result_statuses(results),
            *unprobed_volume_statuses(cfg, resolved, names),
        ],
        output,
        {r.volume_slug: mount_result_json(r) for r in results},
    )


def _print_auth_hint(results: list[MountResult], invocation: Invocation) -> None:
    """Point at ``disks setup-auth`` when polkit refused an unlock or mount."""
    if refused_by_polkit(results):
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
