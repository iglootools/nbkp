import typer
from rich.console import Console

from ...clidisplay import OutputFormat, echo_json
from ...clidisplay.invocation import Invocation, format_troubleshoot_hint
from ...cliwiring.config import (
    load_config_or_exit,
    require_known_syncs,
    resolve_endpoints,
)
from ...cliwiring.mount import managed_mount
from ...cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    MOUNT_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
    SYNC_OPTION,
    UMOUNT_OPTION,
)
from ...cliwiring.preflight import check_all_with_progress
from ...cliwiring.runtime import runtime_of
from ...policy import Strictness
from .. import show_all_syncs
from ..disposition import failed_in_preflight, has_errors
from ..models import ShowResult
from ..output import format_human_show_results
from . import app
from .options import STRICTNESS_OPTION


@app.command("show")
def show_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    sync: SYNC_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    strictness: STRICTNESS_OPTION = Strictness.IGNORE_INACTIVE,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    mount: MOUNT_OPTION = True,
    umount: UMOUNT_OPTION = True,
) -> None:
    """Display snapshot information for each sync endpoint."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_syncs(cfg, sync, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    with managed_mount(
        cfg,
        resolved,
        runtime=runtime,
        mount=mount,
        umount=umount,
        output_format=output,
        strictness=strictness,
    ) as (cfg, mount_observations):
        preflight = check_all_with_progress(
            cfg,
            use_progress=output is OutputFormat.HUMAN,
            only_syncs=sync,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            strictness=strictness,
            exclude_locations=exclude_location or [],
            runtime=runtime,
        )
        results = show_all_syncs(
            cfg,
            preflight.sync_statuses,
            only_syncs=sync,
            resolved_endpoints=resolved,
            strictness=strictness,
            run=runtime.run,
        )
        _print_results(results, output)
        if has_errors(results):
            if output is OutputFormat.HUMAN and failed_in_preflight(
                results, preflight.sync_statuses
            ):
                Console(stderr=True).print(
                    format_troubleshoot_hint(
                        Invocation.of(
                            config,
                            location,
                            exclude_location,
                            network.value if network else None,
                        )
                    ),
                    soft_wrap=True,
                )
            raise typer.Exit(1)


def _print_results(results: list[ShowResult], output: OutputFormat) -> None:
    match output:
        case OutputFormat.JSON:
            echo_json(list(results))
        case OutputFormat.HUMAN:
            Console().print(format_human_show_results(results))
