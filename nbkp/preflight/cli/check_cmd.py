"""The ``nbkp preflight check`` command and its JSON output."""

from collections.abc import Collection

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.invocation import Invocation
from nbkp.cliwiring.mount import managed_mount
from nbkp.cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    MOUNT_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
    UMOUNT_OPTION,
)
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.preflight import check_all_with_progress
from nbkp.cliwiring.runtime import Runtime, runtime_of
from nbkp.config import Config
from nbkp.disks import MountObservation
from nbkp.policy import Strictness
from nbkp.preflight import has_fatal_errors
from nbkp.preflight.cli.options import STRICTNESS_OPTION
from nbkp.preflight.output import format_human_check
from nbkp.preflight.status.result import PreflightResult
from nbkp.remote.endpoints import ResolvedEndpoints


def check_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    strictness: STRICTNESS_OPTION = Strictness.IGNORE_INACTIVE,
    location: LOCATION_OPTION = None,
    *,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    mount: MOUNT_OPTION = True,
    umount: UMOUNT_OPTION = True,
) -> None:
    """
    Verify that volumes are reachable and ready for a run.

    Checks that sentinel files exist, SSH connectivity works, and required tools
    are available. Use this before `run` to confirm everything is ready.
    """
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    invocation = Invocation.of(config, location, exclude_location, network)

    with managed_mount(
        cfg,
        resolved,
        runtime=runtime,
        writes=False,
        mount=mount,
        umount=umount,
        output_format=output,
        strictness=strictness,
    ) as (cfg, mount_observations):
        preflight, has_errors = _check_and_display(
            cfg,
            output,
            strictness,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            invocation=invocation,
            exclude_locations=exclude_location or [],
            runtime=runtime,
        )
        if output is OutputFormat.JSON:
            echo_json(check_json(preflight))
        if has_errors:
            raise typer.Exit(1)


def check_json(preflight: PreflightResult) -> dict[str, object]:
    """
    JSON view of a check: every layer that can carry an error.

    ``ssh_endpoints`` includes the implicit ``localhost`` endpoint, whose
    tool errors (rsync missing, …) block the syncs of local volumes.
    """
    return {
        "ssh_endpoints": list(preflight.ssh_endpoint_statuses.values()),
        "volumes": list(preflight.volume_statuses.values()),
        "syncs": list(preflight.sync_statuses.values()),
    }


def _check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strictness: Strictness,
    only_syncs: list[str] | None = None,
    *,
    resolved_endpoints: ResolvedEndpoints,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
    invocation: Invocation,
    exclude_locations: Collection[str] = (),
    runtime: Runtime,
) -> tuple[PreflightResult, bool]:
    """
    Compute statuses, display human output, and check for errors.

    Returns the preflight result and whether there are fatal errors.
    When *only_syncs* is given, only those syncs (and the volumes
    they reference) are checked.
    """
    preflight = check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
        strictness=strictness,
        exclude_locations=exclude_locations,
        runtime=runtime,
    )

    if output_format is OutputFormat.HUMAN:
        Console().print(
            format_human_check(
                preflight.ssh_endpoint_statuses,
                preflight.volume_statuses,
                preflight.sync_statuses,
                cfg,
                resolved_endpoints=resolved_endpoints,
                strictness=strictness,
                invocation=invocation,
            ),
        )

    return preflight, has_fatal_errors(preflight.sync_statuses, strictness=strictness)
