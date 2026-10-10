"""CLI troubleshoot command."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Annotated

import typer

from ...clihelpers import OutputFormat, echo_json
from ...commands.config import load_config_or_exit, resolve_endpoints
from ...commands.invocation import Invocation
from ...commands.mount import managed_mount
from ...commands.preflight import check_all_with_progress
from ...config.epresolution import NetworkType
from ...policy import Strictness
from ..output import (
    TroubleshootContext,
    collect_issues,
    print_human_troubleshoot,
    troubleshoot_json,
)
from ..status import PreflightResult
from ..strictness import has_fatal_errors
from . import app


@app.command()
def troubleshoot(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
    strictness: Annotated[
        Strictness,
        typer.Option(
            "--strictness",
            "-S",
            help=(
                "Which problems make the command exit non-zero:"
                " ignore-none (any), ignore-inactive (all but expected-inactive,"
                " default), ignore-all (none)"
            ),
        ),
    ] = Strictness.IGNORE_INACTIVE,
    location: Annotated[
        list[str] | None,
        typer.Option(
            "--location",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    exclude_location: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        NetworkType | None,
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
    mount: Annotated[
        bool,
        typer.Option(
            "--mount/--no-mount",
            help="Mount/umount volumes with mount config before checking",
        ),
    ] = True,
    umount: Annotated[
        bool,
        typer.Option(
            "--umount/--no-umount",
            help="Umount after check (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Run the same checks as `check` but displays step-by-step fix instructions for every failure. Useful when `check` reports problems."""
    cfg = load_config_or_exit(config, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    invocation = Invocation.of(
        config, location, exclude_location, network.value if network else None
    )

    with managed_mount(
        cfg,
        resolved,
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
        )
        ctx = TroubleshootContext(
            config=cfg,
            resolved_endpoints=resolved,
            invocation=invocation,
            local_user=getpass.getuser(),
        )
        fatal = has_fatal_errors(preflight.sync_statuses, strictness=strictness)
        _report(preflight, ctx, output, strictness, fatal=fatal)
        if fatal:
            raise typer.Exit(1)


def _report(
    preflight: PreflightResult,
    ctx: TroubleshootContext,
    output: OutputFormat,
    strictness: Strictness,
    *,
    fatal: bool,
) -> None:
    match output:
        case OutputFormat.JSON:
            issues = collect_issues(
                preflight.ssh_endpoint_statuses,
                preflight.volume_statuses,
                preflight.sync_statuses,
                strictness,
            )
            echo_json(troubleshoot_json(issues, ctx, has_fatal_errors=fatal))
        case OutputFormat.HUMAN:
            print_human_troubleshoot(
                preflight.ssh_endpoint_statuses,
                preflight.volume_statuses,
                preflight.sync_statuses,
                ctx.config,
                context=ctx,
                strictness=strictness,
            )
