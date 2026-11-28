"""Rendering of `nbkp run`: its results panel, abort message and JSON output."""

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from nbkp.config import Config
from nbkp.disks import UmountResult
from nbkp.disks.output import umount_result_json
from nbkp.ordering.output import build_rich_tree_sections
from nbkp.policy import Strictness
from nbkp.preflight.output import format_problems
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.run.pipeline import PipelineResult, fatal_sync_errors
from nbkp.sync import result_severity
from nbkp.sync.output import (
    build_human_results_sections,
    build_run_preview_sections,
    sync_result_json,
)
from nbkp.theme import DRY_RUN_LABEL

_ABORT_DETAIL_INDENT = 2


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
        "results": [sync_result_json(r) for r in pipeline.results],
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


def build_results_panel(
    pipeline: PipelineResult,
    cfg: Config,
    resolved: ResolvedEndpoints,
    strictness: Strictness,
    *,
    dry_run: bool,
) -> RenderableType:
    """
    Return the panel of a run that preflight let through.

    The backup graph coloured by each sync's severity, the commands a dry run
    previews, and the sync results.
    """
    sync_severities = {
        r.sync_slug: result_severity(r, strictness) for r in pipeline.results
    }
    sections = [
        *build_rich_tree_sections(cfg, sync_severities),
        # The commands a real run would execute. The script sh generates has no
        # such preview: the script is itself the list of commands it runs.
        *(
            build_run_preview_sections(
                pipeline.attempted_sync_statuses(),
                cfg,
                resolved,
            )
            if dry_run
            else []
        ),
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
        # A Text title: the dry-run label would parse as a markup tag.
        title=Text(
            f"{DRY_RUN_LABEL} Sync Results" if dry_run else "Sync Results",
            style="bold",
        ),
        border_style="cyan",
        padding=(0, 1),
    )
