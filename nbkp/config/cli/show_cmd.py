"""Config show command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat
from .helpers import load_config_or_exit
from ...remote.resolution import resolve_all_endpoints
from ..output import print_human_config
from . import app


@app.command()
def show(
    config: Annotated[
        Optional[Path],
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
    cfg = load_config_or_exit(config)
    output_format = output
    match output_format:
        case OutputFormat.JSON:
            typer.echo(json.dumps(cfg.model_dump(by_alias=True, mode="json"), indent=2))
        case OutputFormat.HUMAN:
            resolved = resolve_all_endpoints(cfg)
            print_human_config(cfg, resolved_endpoints=resolved)
