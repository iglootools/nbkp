"""The ``nbkp snapshots prune`` command."""

import typer
from rich.console import Console

from nbkp.clidisplay import OutputFormat, echo_json
from nbkp.cliwiring.options import (
    CONFIG_OPTION,
    DRY_RUN_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    MOUNT_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
    SYNC_OPTION,
    UMOUNT_OPTION,
)
from nbkp.policy import Strictness
from nbkp.snapshots import prune_all_syncs
from nbkp.snapshots.cli.operation import (
    SnapshotCommandOptions,
    run_snapshot_operation_or_exit,
)
from nbkp.snapshots.cli.options import STRICTNESS_OPTION
from nbkp.snapshots.models import PruneResult
from nbkp.snapshots.output import format_human_prune_results


def prune_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    sync: SYNC_OPTION = None,
    *,
    dry_run: DRY_RUN_OPTION = False,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    strictness: STRICTNESS_OPTION = Strictness.IGNORE_INACTIVE,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    mount: MOUNT_OPTION = True,
    umount: UMOUNT_OPTION = True,
) -> None:
    """
    Remove snapshots beyond the `max-snapshots` limit.

    Normally handled automatically by `run`, but can be invoked manually.
    """
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
        lambda cfg, statuses, resolved, run: prune_all_syncs(
            cfg,
            statuses,
            dry_run=dry_run,
            only_syncs=sync,
            resolved_endpoints=resolved,
            strictness=strictness,
            run=run,
        ),
        lambda results: _print_results(results, dry_run=dry_run, output=output),
        writes=not dry_run,
    )


def _print_results(
    results: list[PruneResult],
    *,
    dry_run: bool,
    output: OutputFormat,
) -> None:
    match (output, dry_run):
        case (OutputFormat.JSON, True):
            # An object, as `disks mount --dry-run` prints: a list has no
            # top-level field to say the run was dry. A real prune keeps its
            # list, so scripts reading it are unaffected.
            echo_json({"dry_run": True, "results": list(results)})
        case (OutputFormat.JSON, False):
            echo_json(list(results))
        case (OutputFormat.HUMAN, _):
            Console().print(format_human_prune_results(results, dry_run=dry_run))
