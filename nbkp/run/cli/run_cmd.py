"""CLI run command."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from ...clihelpers import (
    OutputFormat,
    StepProgressBar,
    echo_json,
    severity_style,
    severity_symbol,
)
from ...clihelpers.invocation import Invocation
from ...commands.config import load_config_or_exit, resolve_endpoints
from ...commands.mount import managed_mount
from ...commands.preflight import check_total
from ...config import Config
from ...ordering.output import build_rich_tree_sections
from ...policy import Strictness
from ...preflight import PreflightResult, SyncStatus
from ...preflight.output import print_human_check
from ...preflight.severity import PreflightError, severity_for_errors
from ...remote.endpoints import NetworkType, ResolvedEndpoints
from ...sync import ProgressMode, SyncResult, result_severity
from ...sync.output import build_human_results_sections
from ..pipeline import PipelineResult, SyncCallbacks, check_and_run

#: Indent of the per-sync lines under the abort message.
_ABORT_DETAIL_INDENT = 2


def run(
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
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Perform a dry run"),
    ] = False,
    sync: Annotated[
        list[str] | None,
        typer.Option("--sync", "-s", help="Sync name(s) to run"),
    ] = None,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format"),
    ] = OutputFormat.HUMAN,
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
            help="Mount/umount volumes with mount config",
        ),
    ] = True,
    umount: Annotated[
        bool,
        typer.Option(
            "--umount/--no-umount",
            help="Umount after sync (use --no-umount for debugging)",
        ),
    ] = True,
) -> None:
    """Execute all active syncs in dependency order. Supports dry-run, progress display, snapshot creation, and automatic pruning."""
    cfg = load_config_or_exit(config, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    invocation = Invocation.of(
        config, location, exclude_location, network.value if network else None
    )
    hint = invocation.command("preflight", "troubleshoot")
    with managed_mount(
        cfg,
        resolved,
        mount=mount,
        umount=umount,
        output_format=output,
        strictness=strictness,
    ) as (cfg, mount_observations):
        display = (
            _HumanProgress(cfg, resolved, sync, progress, strictness, invocation)
            if output is OutputFormat.HUMAN
            else None
        )
        pipeline = check_and_run(
            cfg,
            clock=lambda: datetime.now(UTC),
            platform=sys.platform,
            strictness=strictness,
            dry_run=dry_run,
            only_syncs=sync,
            progress=progress,
            prune=prune,
            on_check_start=display.on_check_start if display else None,
            on_check_end=display.on_check_end if display else None,
            on_checks_done=display.on_checks_done if display else None,
            callbacks=display.sync_callbacks() if display else None,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )
        _print_outcome(pipeline, cfg, resolved, output, strictness, dry_run, hint)
        if pipeline.has_preflight_errors or pipeline.has_sync_failures:
            raise typer.Exit(1)


class _HumanProgress:
    """Live progress display of a human-format run.

    Mutable by necessity: it owns the check progress bar and the sync
    spinner, which callbacks start and stop as the pipeline advances.
    """

    def __init__(
        self,
        cfg: Config,
        resolved: ResolvedEndpoints,
        only_syncs: list[str] | None,
        progress: ProgressMode | None,
        strictness: Strictness,
        invocation: Invocation,
    ) -> None:
        total = check_total(cfg, only_syncs)
        self._cfg = cfg
        self._resolved = resolved
        self._strictness = strictness
        self._invocation = invocation
        self._check_bar = StepProgressBar(total) if total > 0 else None
        self._use_spinner = progress in (None, ProgressMode.NONE)
        self._console = Console()
        self._spinner: Status | None = None

    def on_check_start(self, label: str) -> None:
        if self._check_bar is not None:
            self._check_bar.on_start(f"Checking {label}...")

    def on_check_end(self, label: str, errors: Sequence[PreflightError]) -> None:
        if self._check_bar is not None:
            severity = severity_for_errors(list(errors), self._strictness)
            summary = ", ".join(e.value for e in errors) if errors else None
            self._check_bar.on_end(f"check {label}", severity, summary)

    def on_checks_done(self, preflight: PreflightResult) -> None:
        if self._check_bar is not None:
            self._check_bar.stop()
        print_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            self._cfg,
            resolved_endpoints=self._resolved,
            strictness=self._strictness,
            invocation=self._invocation,
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
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        severity = result_severity(result, self._strictness)
        self._console.print(
            Text.assemble(
                (severity_symbol(severity), severity_style(severity)), f" {slug}"
            )
        )


def _print_outcome(
    pipeline: PipelineResult,
    cfg: Config,
    resolved: ResolvedEndpoints,
    output: OutputFormat,
    strictness: Strictness,
    dry_run: bool,
    hint: str,
) -> None:
    match (output, pipeline.has_preflight_errors):
        case (OutputFormat.JSON, aborted):
            echo_json(_json_payload(pipeline, hint if aborted else None))
        case (OutputFormat.HUMAN, True):
            Console(stderr=True).print(_abort_message(pipeline, strictness, hint))
        case (OutputFormat.HUMAN, False):
            Console().print(
                _results_panel(pipeline, cfg, resolved, strictness, dry_run)
            )


def _json_payload(pipeline: PipelineResult, hint: str | None) -> dict[str, object]:
    return {
        "volumes": list(pipeline.vol_statuses.values()),
        "syncs": list(pipeline.sync_statuses.values()),
        "results": list(pipeline.results),
        **({"hint": hint} if hint is not None else {}),
    }


def _fatal_sync_errors(
    sync_statuses: dict[str, SyncStatus], strictness: Strictness
) -> dict[str, list[str]]:
    """Errors of the syncs that made preflight abort under *strictness*."""
    return {
        slug: sorted(e.value for e in s.errors)
        for slug, s in sync_statuses.items()
        if not s.active
        and (strictness is Strictness.IGNORE_NONE or not s.is_expected_inactive())
    }


def _abort_message(
    pipeline: PipelineResult, strictness: Strictness, hint: str
) -> RenderableType:
    errored = _fatal_sync_errors(pipeline.sync_statuses, strictness)
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
                Text(f"{slug}: {', '.join(errors)}"), (0, 0, 0, _ABORT_DETAIL_INDENT)
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
    dry_run: bool,
) -> RenderableType:
    sync_severities = {
        r.sync_slug: result_severity(r, strictness) for r in pipeline.results
    }
    sections = [
        *build_rich_tree_sections(cfg, sync_severities),
        Text(""),
        *build_human_results_sections(pipeline.results, dry_run, cfg, resolved),
    ]
    return Panel(
        Group(*sections),
        title="[bold]Sync Results[/bold]",
        border_style="cyan",
        padding=(0, 1),
    )
