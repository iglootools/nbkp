"""The ``nbkp preflight check`` command and its JSON output."""

from typing import Annotated

import typer

from nbkp.clidisplay.invocation import Invocation
from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
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
from nbkp.cliwiring.preflight import check_and_display
from nbkp.cliwiring.runtime import runtime_of
from nbkp.policy import Strictness
from nbkp.preflight.status.result import PreflightResult


def check_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    strictness: Annotated[
        Strictness,
        typer.Option(
            "--strictness",
            "-S",
            help=(
                "Which sync problems make the command exit non-zero:"
                " ignore-none (any), ignore-inactive (all but expected-inactive,"
                " default), ignore-all (none). Problems on SSH endpoints or"
                " volumes no sync uses are shown but never fatal"
            ),
        ),
    ] = Strictness.IGNORE_INACTIVE,
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
        preflight, has_errors = check_and_display(
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
