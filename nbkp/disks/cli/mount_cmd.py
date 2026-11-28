"""The ``nbkp disks mount`` command."""

from functools import partial
from typing import Annotated

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.invocation import Invocation, format_troubleshoot_hint
from nbkp.cliwiring.lock import run_lock_or_exit
from nbkp.cliwiring.mount import lifecycle_session
from nbkp.cliwiring.mount_progress import mount_display_names
from nbkp.cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
)
from nbkp.cliwiring.output_format import OutputFormat
from nbkp.cliwiring.runtime import Runtime, runtime_of
from nbkp.config import Config
from nbkp.disks.cli.common import require_known_names, show_plan, show_volume_statuses
from nbkp.disks.cli.hints import print_auth_hint
from nbkp.disks.cli.options import VOLUME_OPTION
from nbkp.disks.context import mount_phase
from nbkp.disks.lifecycle import MountResult, any_failed
from nbkp.disks.mount_checks import check_mount_status
from nbkp.disks.output import mount_result_json
from nbkp.disks.plan import plan_lifecycle
from nbkp.disks.status import mount_result_statuses, unprobed_volume_statuses
from nbkp.remote.endpoints import ResolvedEndpoints


def mount_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    volume: VOLUME_OPTION = None,
    location: LOCATION_OPTION = None,
    *,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help=(
                "Report what would be unlocked and mounted without changing"
                " anything (no passphrase retrieval, no udisksctl unlock/mount)."
            ),
        ),
    ] = False,
) -> None:
    """
    Unlock LUKS and mount volumes.

    Mounts all volumes with mount config, or specific ones via --volume.
    """
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, volume, output)
    resolved = resolve_endpoints(
        cfg,
        location,
        exclude_location,
        network,
        output,
        ssh_config_path=runtime.ssh_config_path(),
    )

    if dry_run:
        show_plan(
            plan_lifecycle(
                cfg,
                resolved,
                mounting=True,
                names=volume,
                probe=partial(check_mount_status, run=runtime.run),
            ),
            mount_display_names(cfg),
            output,
        )
        return

    results = _mount_with_progress(cfg, resolved, volume, output, runtime)
    _show_results(cfg, resolved, results, volume, output)
    invocation = Invocation.of(config, location, exclude_location, network)
    if output is OutputFormat.HUMAN:
        print_auth_hint(results, invocation)
    if any_failed(results):
        if output is OutputFormat.HUMAN:
            Console(stderr=True).print(
                format_troubleshoot_hint(invocation),
                soft_wrap=True,
            )
        raise typer.Exit(1)


def _mount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
    runtime: Runtime,
) -> list[MountResult]:
    with (
        run_lock_or_exit(runtime.run_lock, runtime.run_lock_path(), output),
        lifecycle_session(
            cfg,
            resolved,
            runtime=runtime,
            enabled=output is OutputFormat.HUMAN,
            names=names,
            umounting=False,
        ) as (passphrase_fn, progress),
    ):
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
    """
    Status table: observed, unreachable, unmanaged and excluded volumes.

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
