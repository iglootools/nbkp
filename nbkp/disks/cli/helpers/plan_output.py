import typer
from rich.console import Console
from rich.text import Text

from ....clidisplay import OutputFormat, echo_json, severity_style, severity_symbol
from ....policy import Severity
from ...plan import VolumePlan, plan_incomplete, plan_json


def plan_line(plan: VolumePlan, name: str) -> Text:
    """One human line: ``would unlock, mount <name>``, ``nothing to do``, or
    ``cannot plan`` when the state could not be probed."""
    severity = Severity.ERROR if plan.probe_failed else Severity.OK
    verbs = ", ".join(action.value for action in plan.actions)
    return Text.assemble(
        (severity_symbol(severity), severity_style(severity)),
        (
            f" cannot plan {name}"
            if plan.probe_failed
            else f" would {verbs} {name}"
            if plan.actions
            else f" nothing to do for {name}"
        ),
        *([(f" ({plan.detail})", "dim")] if plan.detail else []),
    )


def show_plan(
    plans: list[VolumePlan],
    display_names: dict[str, str],
    output_format: OutputFormat,
) -> None:
    """Print a dry-run plan as lines (human) or ``{"dry_run": …}`` (JSON).

    Exits 1 when the plan is incomplete (:func:`plan_incomplete`).
    """
    match output_format:
        case OutputFormat.JSON:
            echo_json({"dry_run": True, "volumes": plan_json(plans)})
        case OutputFormat.HUMAN:
            console = Console()
            console.print(Text("Dry run — no udisksctl changes made.", style="bold"))
            for plan in plans:
                console.print(
                    plan_line(
                        plan, display_names.get(plan.volume_slug, plan.volume_slug)
                    )
                )
    if plan_incomplete(plans):
        raise typer.Exit(1)
