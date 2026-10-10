"""Rendering of ``--dry-run`` lifecycle plans."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from ....clihelpers import OutputFormat, echo_json, severity_style, severity_symbol
from ....policy import Severity
from ...plan import VolumePlan, plan_json


def _plan_line(plan: VolumePlan, name: str) -> Text:
    """One human line: ``would unlock, mount <name>`` or ``nothing to do``."""
    severity = Severity.ERROR if plan.probe_failed else Severity.OK
    verbs = ", ".join(action.value for action in plan.actions)
    return Text.assemble(
        (severity_symbol(severity), severity_style(severity)),
        f" would {verbs} {name}" if plan.actions else f" nothing to do for {name}",
        *([(f" ({plan.detail})", "dim")] if plan.detail else []),
    )


def show_plan(
    plans: list[VolumePlan],
    display_names: dict[str, str],
    output_format: OutputFormat,
) -> None:
    """Print a dry-run plan as lines (human) or ``{"dry_run": …}`` (JSON)."""
    match output_format:
        case OutputFormat.JSON:
            echo_json({"dry_run": True, "volumes": plan_json(plans)})
        case OutputFormat.HUMAN:
            console = Console()
            console.print(Text("Dry run — no udisksctl changes made.", style="bold"))
            for plan in plans:
                console.print(
                    _plan_line(
                        plan, display_names.get(plan.volume_slug, plan.volume_slug)
                    )
                )
