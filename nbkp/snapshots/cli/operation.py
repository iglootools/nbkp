"""
The flow ``snapshots show`` and ``snapshots prune`` share.

Load the config, take the run lock and mount what needs mounting, run
preflight, apply the command's operation to the sync statuses, print the
results, and exit 1 on an error, pointing at ``preflight troubleshoot`` when
preflight caused it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from nbkp.clidisplay.invocation import Invocation, format_troubleshoot_hint
from nbkp.cliwiring.config import (
    load_config_or_exit,
    require_known_syncs,
    resolve_endpoints,
)
from nbkp.cliwiring.mount import managed_mount
from nbkp.cliwiring.output_format import OutputFormat
from nbkp.cliwiring.preflight import check_all_with_progress
from nbkp.cliwiring.runtime import runtime_of
from nbkp.config import Config
from nbkp.policy import Strictness
from nbkp.preflight import SyncStatus
from nbkp.remote.dispatch import VolumeCommandRunner
from nbkp.remote.endpoints import NetworkType, ResolvedEndpoints
from nbkp.snapshots.disposition import failed_in_preflight, has_errors
from nbkp.snapshots.models import PruneResult, ShowResult


@dataclass(frozen=True)
class SnapshotCommandOptions:
    """The options ``snapshots show`` and ``prune`` both take."""

    config: Path | None
    sync: list[str] | None
    output: OutputFormat
    strictness: Strictness
    location: list[str] | None
    exclude_location: list[str] | None
    network: NetworkType | None
    mount: bool
    umount: bool


type SnapshotOperationFn[R: ShowResult | PruneResult] = Callable[
    [Config, dict[str, SyncStatus], ResolvedEndpoints, VolumeCommandRunner],
    list[R],
]


def run_snapshot_operation_or_exit[R: ShowResult | PruneResult](
    ctx: typer.Context,
    options: SnapshotCommandOptions,
    operation_fn: SnapshotOperationFn[R],
    print_fn: Callable[[list[R]], None],
    *,
    writes: bool,
) -> None:
    """
    Run *operation_fn* on the preflight statuses and print with *print_fn*.

    *writes* says whether *operation_fn* deletes snapshots; the command then
    holds the run lock even when it neither mounts nor umounts.
    """
    runtime = runtime_of(ctx)
    output = options.output
    cfg = load_config_or_exit(runtime, options.config, output)
    require_known_syncs(cfg, options.sync, output)
    resolved = resolve_endpoints(
        cfg,
        options.location,
        options.exclude_location,
        options.network,
        output,
    )
    with managed_mount(
        cfg,
        resolved,
        runtime=runtime,
        writes=writes,
        mount=options.mount,
        umount=options.umount,
        output_format=output,
        strictness=options.strictness,
    ) as (cfg, mount_observations):
        preflight = check_all_with_progress(
            cfg,
            use_progress=output is OutputFormat.HUMAN,
            only_syncs=options.sync,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            strictness=options.strictness,
            exclude_locations=options.exclude_location or [],
            runtime=runtime,
        )
        results = operation_fn(cfg, preflight.sync_statuses, resolved, runtime.run)
        print_fn(results)
        if has_errors(results):
            if output is OutputFormat.HUMAN and failed_in_preflight(
                results,
                preflight.sync_statuses,
            ):
                _print_troubleshoot_hint(options)
            raise typer.Exit(1)


def _print_troubleshoot_hint(options: SnapshotCommandOptions) -> None:
    Console(stderr=True).print(
        format_troubleshoot_hint(
            Invocation.of(
                options.config,
                options.location,
                options.exclude_location,
                options.network,
            ),
        ),
        soft_wrap=True,
    )
