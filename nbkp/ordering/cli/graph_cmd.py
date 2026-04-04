"""CLI graph command for sync dependency visualization."""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...clihelpers import OutputFormat
from ...config.clihelpers import load_config_or_exit
from ..output import (
    build_graph_json,
    print_mermaid_ascii_graph,
    print_mermaid_graph,
    print_rich_tree_graph,
)
from . import app


class GraphFormat(str, enum.Enum):
    """Graph output format."""

    RICH_TREE = "rich-tree"
    MERMAID_ASCII = "mermaid-ascii"
    MERMAID = "mermaid"


@app.command()
def graph(
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
    format: Annotated[
        GraphFormat,
        typer.Option("--format", "-f", help="Graph format (human output only)"),
    ] = GraphFormat.RICH_TREE,
) -> None:
    """Display the backup chain as a graph."""
    cfg = load_config_or_exit(config)
    match output:
        case OutputFormat.JSON:
            typer.echo(json.dumps(build_graph_json(cfg), indent=2))
        case OutputFormat.HUMAN:
            match format:
                case GraphFormat.RICH_TREE:
                    print_rich_tree_graph(cfg)
                case GraphFormat.MERMAID_ASCII:
                    print_mermaid_ascii_graph(cfg)
                case GraphFormat.MERMAID:
                    print_mermaid_graph(cfg)
