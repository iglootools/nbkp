import typer
from rich.console import Console

from ...clidisplay import OutputFormat, echo_json
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
from ...policy import Strictness
from .. import show_all_syncs
from ..models import ShowResult
from ..output import format_human_show_results
from . import app
from .operation import SnapshotCommandOptions, run_snapshot_operation_or_exit
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
    options = SnapshotCommandOptions(
        config=config,
        sync=sync,
        output=output,
        strictness=strictness,
        location=location,
        exclude_location=exclude_location,
        network=network,
        mount=mount,
        umount=umount,
    )
    run_snapshot_operation_or_exit(
        ctx,
        options,
        lambda cfg, statuses, resolved, run: show_all_syncs(
            cfg,
            statuses,
            only_syncs=sync,
            resolved_endpoints=resolved,
            strictness=strictness,
            run=run,
        ),
        lambda results: _print_results(results, output),
    )


def _print_results(results: list[ShowResult], output: OutputFormat) -> None:
    match output:
        case OutputFormat.JSON:
            echo_json(list(results))
        case OutputFormat.HUMAN:
            Console().print(format_human_show_results(results))
