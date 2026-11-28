"""The ``nbkp preflight troubleshoot`` command: preflight checks with fixes."""

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.invocation import Invocation
from nbkp.cliwiring.mount import managed_mount_or_exit
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
from nbkp.cliwiring.runtime import runtime_of
from nbkp.policy import Strictness
from nbkp.preflight.cli.options import STRICTNESS_OPTION
from nbkp.preflight.output import (
    TroubleshootContext,
    format_human_troubleshoot,
    troubleshoot_json,
)
from nbkp.preflight.strictness import has_fatal_errors
from nbkp.preflight.troubleshoot import (
    TroubleshootIssue,
    collect_issues,
    gather_remediation_facts,
)


def troubleshoot_cmd(
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
    Run the same checks as `check`, with step-by-step fixes for every failure.

    Useful when `check` reports problems.
    """
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    resolved = resolve_endpoints(
        cfg,
        location,
        exclude_location,
        network,
        output,
        ssh_config_path=runtime.ssh_config_path(),
    )
    invocation = Invocation.of(config, location, exclude_location, network)

    with managed_mount_or_exit(
        cfg,
        resolved,
        runtime=runtime,
        writes=False,
        mount=mount,
        umount=umount,
        output_format=output,
        strictness=strictness,
    ) as (cfg, mount_observations):
        preflight = check_all_with_progress(
            cfg,
            use_progress=output is OutputFormat.HUMAN,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            strictness=strictness,
            exclude_locations=exclude_location or [],
            runtime=runtime,
        )
        issues = collect_issues(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            strictness,
        )
        local_user = runtime.local_user()
        context = TroubleshootContext(
            config=cfg,
            resolved_endpoints=resolved,
            invocation=invocation,
            local_user=local_user,
            facts=gather_remediation_facts(
                issues,
                cfg,
                resolved,
                local_user,
                run=runtime.run,
            ),
        )
        fatal = has_fatal_errors(preflight.sync_statuses, strictness=strictness)
        _report(issues, context, output, fatal=fatal)
        if fatal:
            raise typer.Exit(1)


def _report(
    issues: list[TroubleshootIssue],
    ctx: TroubleshootContext,
    output: OutputFormat,
    *,
    fatal: bool,
) -> None:
    match output:
        case OutputFormat.JSON:
            echo_json(troubleshoot_json(issues, ctx, has_fatal_errors=fatal))
        case OutputFormat.HUMAN:
            Console().print(format_human_troubleshoot(issues, ctx))
