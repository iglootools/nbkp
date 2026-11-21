from functools import partial
from typing import Annotated

import typer
from rich.console import Console

from ...clidisplay import OutputFormat
from ...clidisplay.invocation import Invocation, format_troubleshoot_hint
from ...cliwiring.config import load_config_or_exit, resolve_endpoints
from ...cliwiring.disks import probe_and_show_status, require_known_names, show_plan
from ...cliwiring.mount_progress import LifecycleProgress, mount_display_names
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
from ..lifecycle import UmountResult, any_failed, umount_volumes
from ..mount_checks import check_mount_status
from ..plan import plan_lifecycle
from . import app
from .options import NAME_OPTION


@app.command("umount")
def umount_cmd(
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
                "Report what would be unmounted and locked without changing"
                " anything (no udisksctl unmount/lock)."
                " Long form only: -n is --name here."
            ),
        ),
    ] = False,
) -> None:
    """Umount volumes and lock LUKS. Umounts all volumes with mount config, or specific ones via --name."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)

    if dry_run:
        show_plan(
            plan_lifecycle(
                cfg,
                resolved,
                mounting=False,
                names=name,
                probe=partial(check_mount_status, run=runtime.run),
            ),
            mount_display_names(cfg),
            output,
        )
        return

    results = _umount_with_progress(cfg, resolved, name, output, runtime)
    probe_and_show_status(
        cfg, resolved, output, name, umount_results=results, run=runtime.run
    )
    if any_failed(results):
        if output is OutputFormat.HUMAN:
            Console(stderr=True).print(
                format_troubleshoot_hint(
                    Invocation.of(
                        config,
                        location,
                        exclude_location,
                        network,
                    )
                ),
                soft_wrap=True,
            )
        raise typer.Exit(1)


def _umount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
    runtime: Runtime,
) -> list[UmountResult]:
    """Umount and lock, with a progress bar for humans."""
    progress = LifecycleProgress.create(
        cfg,
        resolved,
        enabled=output is OutputFormat.HUMAN,
        names=names,
        credentials=False,
        mounting=False,
    )
    try:
        return umount_volumes(
            cfg,
            resolved,
            names=names,
            on_umount_start=progress.on_umount_start,
            on_umount_end=progress.on_umount_end,
            run=runtime.run,
        )
    finally:
        progress.stop_all()
