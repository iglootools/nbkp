from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ...clidisplay import OutputFormat, echo_json
from ...cliwiring.config import load_config_or_exit, resolve_endpoints
from ...cliwiring.runtime import runtime_of
from ..output import format_human_config
from . import app


@app.command("show")
def show_cmd(
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
) -> None:
    """Load, validate, and render the config as tables or JSON. Useful for verifying that inheritance, filters, and cross-references resolve correctly."""
    cfg = load_config_or_exit(runtime_of(ctx), config, output)
    match output:
        case OutputFormat.JSON:
            echo_json(cfg.model_dump(by_alias=True, mode="json"))
        case OutputFormat.HUMAN:
            resolved = resolve_endpoints(cfg, None, None, None)
            Console().print(format_human_config(cfg, resolved_endpoints=resolved))
