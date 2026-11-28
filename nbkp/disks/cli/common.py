"""
Wiring the ``disks`` commands ``mount``, ``umount`` and ``status`` share.

Shared: the status table, the dry-run plan and the ``--volume`` check.
"""

from collections.abc import Mapping, Sequence

import typer
from rich.console import Console

from nbkp.cliwiring.config import require_known
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.progress import StepProgressBar
from nbkp.config import Config, Volume
from nbkp.disks import (
    ProbeEndFn,
    UmountResult,
    VolumePlan,
    VolumeStatus,
    display_name,
    managed_volumes,
    plan_incomplete,
    plan_json,
    probe_volume_statuses,
    unknown_volume_names,
)
from nbkp.disks.output import (
    build_volume_status_table,
    format_plan_line,
    umount_result_json,
    volume_status_detail,
    volume_status_json,
)
from nbkp.policy import Severity
from nbkp.remote.dispatch import VolumeCommandRunner
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.theme import dry_run_labeled


def show_volume_statuses(
    cfg: Config,
    statuses: Sequence[VolumeStatus],
    output_format: OutputFormat,
    json_extras_by_slug: Mapping[str, dict[str, object]] | None = None,
) -> None:
    """
    Print the status table, or one ``-o json`` entry per volume.

    Each JSON entry is merged with its *json_extras_by_slug* entry (why a mount or
    umount failed).
    """
    extras = json_extras_by_slug or {}
    match output_format:
        case OutputFormat.JSON:
            echo_json(
                [
                    {**volume_status_json(s), **extras.get(s.volume_slug, {})}
                    for s in statuses
                ],
            )
        case OutputFormat.HUMAN:
            if statuses:
                display_names = {
                    slug: display_name(vol) for slug, vol in cfg.volumes.items()
                }
                Console().print(build_volume_status_table(statuses, display_names))
            else:
                typer.echo("No volumes found.")


def _status_line(volume: Volume) -> str:
    """
    Format a volume's status line, prefixed with ``status``.

    The prefix tells the line apart from the ``mount`` / ``umount`` action lines the
    same console prints (``disks umount`` runs the umount step and then this probe
    back-to-back).
    """
    return f"status {display_name(volume)}"


def _on_probe_end(bar: StepProgressBar) -> ProbeEndFn:
    def on_probe_end(volume: Volume, status: VolumeStatus) -> None:
        detail = volume_status_detail(status)
        severity = Severity.OK if detail is None else Severity.ERROR
        bar.on_end(_status_line(volume), severity, detail)

    return on_probe_end


def _probe_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    names: list[str] | None,
    output_format: OutputFormat,
    run: VolumeCommandRunner,
) -> list[VolumeStatus]:
    managed = managed_volumes(cfg, resolved, names)
    bar = (
        StepProgressBar(len(managed))
        if output_format is OutputFormat.HUMAN and managed
        else None
    )
    try:
        return probe_volume_statuses(
            cfg,
            resolved,
            names,
            run=run,
            on_probe_start=(
                (lambda vol: bar.on_start(_status_line(vol))) if bar else None
            ),
            on_probe_end=_on_probe_end(bar) if bar else None,
        )
    finally:
        if bar is not None:
            bar.stop()


def probe_and_show_status(
    cfg: Config,
    resolved: ResolvedEndpoints,
    output_format: OutputFormat,
    names: list[str] | None = None,
    umount_results: Sequence[UmountResult] = (),
    *,
    run: VolumeCommandRunner,
) -> None:
    """
    Probe and show the mount status of the volumes, through *run*.

    *umount_results* are the umounts just attempted: under ``-o json`` the
    entry of a volume whose umount failed also says why (``disks umount``).
    """
    show_volume_statuses(
        cfg,
        _probe_with_progress(cfg, resolved, names, output_format, run),
        output_format,
        {r.volume_slug: umount_result_json(r) for r in umount_results},
    )


def require_known_names(
    cfg: Config,
    names: list[str] | None,
    output_format: OutputFormat,
) -> None:
    """Exit 2 when a ``--volume`` matches no volume."""
    require_known(
        "volume",
        unknown_volume_names(cfg, names),
        cfg.volumes,
        output_format,
    )


def show_plan(
    plans: list[VolumePlan],
    display_names: dict[str, str],
    output_format: OutputFormat,
) -> None:
    """
    Print a dry-run plan as lines (human) or ``{"dry_run": …}`` (JSON).

    Exits 1 when the plan is incomplete (:func:`plan_incomplete`).
    """
    match output_format:
        case OutputFormat.JSON:
            echo_json({"dry_run": True, "volumes": plan_json(plans)})
        case OutputFormat.HUMAN:
            console = Console()
            if not plans:
                # As the real run does, rather than print nothing.
                console.print(dry_run_labeled("No volumes found."))
            for plan in plans:
                console.print(
                    format_plan_line(
                        plan,
                        display_names.get(plan.volume_slug, plan.volume_slug),
                    ),
                )
    if plan_incomplete(plans):
        raise typer.Exit(1)
