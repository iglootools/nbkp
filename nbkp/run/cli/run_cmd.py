"""The ``nbkp run`` command: preflight, then every active sync, with progress."""

from collections.abc import Sequence
from contextlib import nullcontext
from types import TracebackType
from typing import Annotated, Self

import typer
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from nbkp.clidisplay import (
    OutputFormat,
    StepProgressBar,
    echo_json,
    severity_style,
    severity_symbol,
)
from nbkp.clidisplay.invocation import Invocation
from nbkp.cliwiring.config import (
    load_config_or_exit,
    require_known_syncs,
    resolve_endpoints,
)
from nbkp.cliwiring.mount import format_umount_failures, managed_mount
from nbkp.cliwiring.mount_progress import mount_display_names
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
from nbkp.cliwiring.preflight import end_check_step, start_check_step
from nbkp.cliwiring.runtime import runtime_of
from nbkp.config import Config
from nbkp.disks import UmountResult
from nbkp.disks.output import umount_result_json
from nbkp.ordering.output import build_rich_tree_sections
from nbkp.policy import Strictness
from nbkp.preflight import (
    PreflightErrorKind,
    PreflightResult,
    check_step_count,
)
from nbkp.preflight.output import format_human_check, format_problems
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.run.pipeline import (
    PipelineResult,
    SyncCallbacks,
    check_and_run,
    fatal_sync_errors,
    run_failed,
)
from nbkp.sync import ProgressMode, RunOptions, SyncResult, result_severity
from nbkp.sync.output import build_human_results_sections

_ABORT_DETAIL_INDENT = 2


def run_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    *,
    dry_run: DRY_RUN_OPTION = False,
    sync: SYNC_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    progress: Annotated[
        ProgressMode | None,
        typer.Option(
            "--progress",
            "-p",
            help=("Progress mode: none, overall, per-file, or full"),
        ),
    ] = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune/--no-prune",
            help="Prune old snapshots after sync",
        ),
    ] = True,
    strictness: Annotated[
        Strictness,
        typer.Option(
            "--strictness",
            "-S",
            help=(
                "How to handle preflight errors:"
                " ignore-none (all errors fatal),"
                " ignore-inactive (skip expected-inactive, default),"
                " ignore-all (attempt syncs despite preflight errors)"
            ),
        ),
    ] = Strictness.IGNORE_INACTIVE,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    mount: MOUNT_OPTION = True,
    umount: UMOUNT_OPTION = True,
) -> None:
    """
    Execute all active syncs in dependency order.

    Supports dry-run, progress display, snapshot creation, and automatic pruning.
    """
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_syncs(cfg, sync, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    invocation = Invocation.of(config, location, exclude_location, network)
    hint = _troubleshoot_hint(invocation, strictness, mount=mount)
    # Filled when the volumes are unmounted, so the outcome is printed after
    # the block: a drive left mounted or unlocked is part of the outcome.
    umount_results: list[UmountResult] = []
    with (
        managed_mount(
            cfg,
            resolved,
            runtime=runtime,
            writes=not dry_run,
            mount=mount,
            umount=umount,
            output_format=output,
            strictness=strictness,
            umount_results=umount_results,
        ) as (cfg, mount_observations),
        (
            _HumanProgress(
                cfg,
                resolved,
                exclude_location or [],
                sync,
                progress,
                strictness=strictness,
                invocation=invocation,
            )
            if output is OutputFormat.HUMAN
            else nullcontext()
        ) as display,
    ):
        options = RunOptions(
            clock=runtime.clock,
            platform=runtime.platform,
            strictness=strictness,
            dry_run=dry_run,
            only_syncs=sync,
            progress=progress,
            prune=prune,
            resolved_endpoints=resolved,
            rsync=runtime.rsync,
            run_command=runtime.run,
        )
        pipeline = check_and_run(
            cfg,
            options,
            on_check_start=display.on_check_start if display else None,
            on_check_end=display.on_check_end if display else None,
            on_checks_done=display.on_checks_done if display else None,
            callbacks=display.sync_callbacks() if display else None,
            mount_observations=mount_observations,
            exclude_locations=exclude_location or [],
            which=runtime.which,
        )
    _print_outcome(
        pipeline,
        cfg,
        resolved,
        output,
        strictness,
        dry_run=dry_run,
        hint=hint,
        umount_results=umount_results,
    )
    if run_failed(pipeline, umount_results):
        raise typer.Exit(1)


def _troubleshoot_hint(
    invocation: Invocation,
    strictness: Strictness,
    *,
    mount: bool,
) -> str:
    """
    Build the ``preflight troubleshoot`` command that explains an aborted run.

    It judges problems at the run's strictness, and leaves the drives alone when the
    run did. It cannot narrow to the run's ``--sync`` selection, which ``troubleshoot``
    does not take.
    """
    return invocation.command(
        "preflight",
        "troubleshoot",
        *(
            ["-S", strictness.value]
            if strictness is not Strictness.IGNORE_INACTIVE
            else []
        ),
        *([] if mount else ["--no-mount"]),
    )


class _HumanProgress:
    """
    Live progress display of a human-format run.

    Mutable by necessity: it owns the check progress bar and the sync
    spinner, which callbacks start and stop as the pipeline advances. Used as a
    context manager, which stops both however the pipeline ends.
    """

    def __init__(
        self,
        cfg: Config,
        resolved: ResolvedEndpoints,
        exclude_locations: list[str],
        only_syncs: list[str] | None,
        progress: ProgressMode | None,
        *,
        strictness: Strictness,
        invocation: Invocation,
    ) -> None:
        total = check_step_count(
            cfg,
            resolved_endpoints=resolved,
            only_syncs=only_syncs,
            exclude_locations=exclude_locations,
        )
        self._cfg = cfg
        self._resolved = resolved
        self._strictness = strictness
        self._invocation = invocation
        self._check_bar = StepProgressBar(total) if total > 0 else None
        self._use_spinner = progress in (None, ProgressMode.NONE)
        self._console = Console()
        self._spinner: Status | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # A check or sync that raised leaves its bar or spinner running, and
        # its refresh thread would keep redrawing over the traceback.
        if self._check_bar is not None:
            self._check_bar.stop()
        self._stop_spinner()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def on_check_start(self, label: str) -> None:
        if self._check_bar is not None:
            start_check_step(self._check_bar, label)

    def on_check_end(self, label: str, errors: Sequence[PreflightErrorKind]) -> None:
        if self._check_bar is not None:
            end_check_step(self._check_bar, label, errors, self._strictness)

    def on_checks_done(self, preflight: PreflightResult) -> None:
        if self._check_bar is not None:
            self._check_bar.stop()
        self._console.print(
            format_human_check(
                preflight.ssh_endpoint_statuses,
                preflight.volume_statuses,
                preflight.sync_statuses,
                self._cfg,
                resolved_endpoints=self._resolved,
                strictness=self._strictness,
                invocation=self._invocation,
            ),
        )

    def sync_callbacks(self) -> SyncCallbacks:
        return SyncCallbacks(
            on_rsync_output=(
                None if self._use_spinner else lambda c: typer.echo(c, nl=False)
            ),
            on_sync_start=self._on_sync_start,
            on_sync_end=self._on_sync_end,
        )

    def _on_sync_start(self, slug: str) -> None:
        if self._use_spinner:
            self._spinner = self._console.status(Text(f"Syncing {slug}..."))
            self._spinner.start()
        else:
            self._console.print(Text(f"Syncing {slug}..."))

    def _on_sync_end(self, slug: str, result: SyncResult) -> None:
        self._stop_spinner()
        severity = result_severity(result, self._strictness)
        self._console.print(
            Text.assemble(
                (severity_symbol(severity), severity_style(severity)),
                f" {slug}",
            ),
        )


def _print_outcome(
    pipeline: PipelineResult,
    cfg: Config,
    resolved: ResolvedEndpoints,
    output: OutputFormat,
    strictness: Strictness,
    *,
    dry_run: bool,
    hint: str,
    umount_results: list[UmountResult],
) -> None:
    match (output, pipeline.has_preflight_errors):
        case (OutputFormat.JSON, aborted):
            echo_json(
                run_json(
                    pipeline,
                    hint if aborted else None,
                    umount_results,
                    dry_run=dry_run,
                ),
            )
        case (OutputFormat.HUMAN, True):
            Console(stderr=True).print(abort_message(pipeline, strictness, hint))
        case (OutputFormat.HUMAN, False):
            Console().print(
                _results_panel(pipeline, cfg, resolved, strictness, dry_run=dry_run),
            )
    if output is OutputFormat.HUMAN:
        Console(stderr=True).print(
            format_umount_failures(umount_results, mount_display_names(cfg)),
        )


def run_json(
    pipeline: PipelineResult,
    hint: str | None,
    umount_results: list[UmountResult],
    *,
    dry_run: bool,
) -> dict[str, object]:
    """
    Build ``run -o json``.

    ``"dry_run": true`` for a dry run, the preflight statuses, the sync results, the
    umount outcome of each mount-managed volume, and, when preflight aborted the run,
    the troubleshoot command (*hint*).
    """
    return {
        **({"dry_run": True} if dry_run else {}),
        "volumes": list(pipeline.preflight.volume_statuses.values()),
        "syncs": list(pipeline.preflight.sync_statuses.values()),
        "results": list(pipeline.results),
        "umount": [
            {"volume": r.volume_slug, "success": r.success, **umount_result_json(r)}
            for r in umount_results
        ],
        **({"hint": hint} if hint is not None else {}),
    }


def abort_message(
    pipeline: PipelineResult,
    strictness: Strictness,
    hint: str,
) -> RenderableType:
    """
    Explain why preflight aborted the run.

    Each sync whose errors are fatal under *strictness*, and the troubleshoot command
    (*hint*) that explains them.
    """
    errored = fatal_sync_errors(pipeline.preflight.sync_statuses, strictness)
    plural = "s" if len(errored) != 1 else ""
    # Text, not markup: slugs and error values are not nbkp-authored markup.
    return Group(
        Text(""),
        Text.assemble(
            ("Aborting:", "bold red"),
            f" preflight checks found errors in {len(errored)} sync{plural}:",
        ),
        *(
            Padding(
                Text(f"{slug}: {format_problems(errors)}"),
                (0, 0, 0, _ABORT_DETAIL_INDENT),
            )
            for slug, errors in errored.items()
        ),
        Text.assemble("Run ", (hint, "bold"), " for step-by-step fixes."),
    )


def _results_panel(
    pipeline: PipelineResult,
    cfg: Config,
    resolved: ResolvedEndpoints,
    strictness: Strictness,
    *,
    dry_run: bool,
) -> RenderableType:
    sync_severities = {
        r.sync_slug: result_severity(r, strictness) for r in pipeline.results
    }
    sections = [
        *build_rich_tree_sections(cfg, sync_severities),
        Text(""),
        *build_human_results_sections(
            pipeline.results,
            dry_run=dry_run,
            config=cfg,
            resolved_endpoints=resolved,
        ),
    ]
    return Panel(
        Group(*sections),
        title="[bold]Sync Results[/bold]",
        border_style="cyan",
        padding=(0, 1),
    )
