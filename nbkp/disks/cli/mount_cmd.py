from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from ...clidisplay import OutputFormat, echo_json
from ...clidisplay.invocation import Invocation, print_troubleshoot_hint
from ...cliwiring.config import load_config_or_exit, resolve_endpoints
from ...cliwiring.credentials import prompt_passphrase
from ...cliwiring.mount_progress import LifecycleProgress, mount_display_names
from ...cliwiring.runtime import Runtime, runtime_of
from ...config import Config
from ...credentials import build_passphrase_fn, prefetch_passphrases
from ...remote.endpoints import NetworkType, ResolvedEndpoints
from ..lifecycle import MountResult, any_failed, mount_volumes, refused_by_polkit
from ..mount_checks import check_mount_status
from ..observation import build_mount_observations
from ..output import (
    build_mount_status_json,
    mount_failure_detail,
    mount_result_json,
)
from ..plan import plan_lifecycle
from . import app
from .helpers import (
    ErrorStatus,
    error_label,
    require_known_names,
    show_status_table,
    unprobed_statuses,
)
from .helpers.plan_output import show_plan


@app.command("mount")
def mount_cmd(
    ctx: typer.Context,
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
    name: Annotated[
        list[str] | None,
        typer.Option("--name", "-n", help="Volume name(s) to mount"),
    ] = None,
    location: Annotated[
        list[str] | None,
        typer.Option("--location", "-l", help="Prefer endpoints at these locations"),
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
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Report what would be unlocked and mounted without changing"
                " anything (no passphrase retrieval, no udisksctl unlock/mount)."
                " Long form only: -n is --name here."
            ),
        ),
    ] = False,
) -> None:
    """Unlock LUKS and mount volumes. Mounts all volumes with mount config, or specific ones via --name."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)

    if dry_run:
        show_plan(
            plan_lifecycle(
                cfg,
                resolved,
                mounting=True,
                names=name,
                probe=partial(check_mount_status, run=runtime.run),
            ),
            mount_display_names(cfg),
            output,
        )
        return

    results = _mount_with_progress(cfg, resolved, name, output, runtime)
    _show_results(cfg, resolved, results, name, output)
    invocation = Invocation.of(
        config, location, exclude_location, network.value if network else None
    )
    if output is OutputFormat.HUMAN:
        _print_auth_hint(results, invocation)
    if any_failed(results):
        if output is OutputFormat.HUMAN:
            print_troubleshoot_hint(invocation)
        raise typer.Exit(1)


def _mount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output: OutputFormat,
    runtime: Runtime,
) -> list[MountResult]:
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider,
        cfg.credential_command,
        prompt=prompt_passphrase,
        environ=runtime.environ,
        run=runtime.run_credential_command,
    )
    progress = LifecycleProgress.create(
        cfg,
        resolved,
        enabled=output is OutputFormat.HUMAN,
        names=names,
        umounting=False,
    )
    # try/finally instead of `with` because the bars are conditionally
    # created (None when output is JSON), and cache.clear() must also run.
    try:
        # Retrieve every configured passphrase before touching a device —
        # deliberately including the ones --name excludes and the ones whose
        # drive is absent, so one approval pass covers every drive.
        prefetch_passphrases(
            cfg,
            passphrase_fn,
            on_prefetch_start=progress.on_prefetch_start,
            on_prefetch_end=progress.on_prefetch_end,
        )
        return mount_volumes(
            cfg,
            resolved,
            passphrase_fn,
            names=names,
            on_mount_start=progress.on_mount_start,
            on_mount_end=progress.on_mount_end,
            run=runtime.run,
        )
    finally:
        progress.stop_all()
        cache.clear()


def _show_results(
    cfg: Config,
    resolved: ResolvedEndpoints,
    results: list[MountResult],
    names: list[str] | None,
    output: OutputFormat,
) -> None:
    """Status table: observed, unreachable, unmanaged and excluded volumes.

    The JSON entries are built from the results themselves, so that they
    carry why a mount failed, not only the state it left.
    """
    display_names = mount_display_names(cfg)
    match output:
        case OutputFormat.JSON:
            echo_json(
                [
                    *(
                        mount_result_entry(
                            display_names.get(r.volume_slug, r.volume_slug), r
                        )
                        for r in results
                    ),
                    *build_mount_status_json(unprobed_statuses(cfg, resolved, names)),
                ]
            )
        case OutputFormat.HUMAN:
            _show_human_results(cfg, resolved, results, names, display_names)


def mount_result_entry(name: str, result: MountResult) -> dict[str, object]:
    """The ``-o json`` entry of a volume *disks mount* acted on: the state it
    left and, on failure, why."""
    return {
        "volume": name,
        "device_present": result.device_present,
        "luks_unlocked": result.luks_unlocked,
        "mounted": result.mounted,
        **mount_result_json(result),
    }


def _show_human_results(
    cfg: Config,
    resolved: ResolvedEndpoints,
    results: list[MountResult],
    names: list[str] | None,
    display_names: dict[str, str],
) -> None:
    observations = build_mount_observations(results)
    show_status_table(
        [
            *(
                (display_names.get(slug, slug), obs)
                for slug, obs in observations.items()
            ),
            *(
                (
                    error_label(
                        display_names.get(r.volume_slug, r.volume_slug),
                        mount_failure_detail(r),
                    ),
                    ErrorStatus(),
                )
                for r in results
                if not r.success and r.volume_slug not in observations
            ),
            *unprobed_statuses(cfg, resolved, names),
        ],
        OutputFormat.HUMAN,
    )


def _print_auth_hint(results: list[MountResult], invocation: Invocation) -> None:
    """Point at ``disks setup-auth`` when polkit refused an unlock or mount."""
    if refused_by_polkit(results):
        console = Console()
        console.print(
            "udisks refused the operation (no polkit rule). Generate one with:"
        )
        # soft_wrap: the command must stay on one line to copy-paste.
        console.print(
            Text(
                invocation.command("disks", "setup-auth", endpoint_flags=False),
                style="bold",
            ),
            soft_wrap=True,
        )
