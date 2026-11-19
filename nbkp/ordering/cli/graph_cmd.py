import enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ...clidisplay import OutputFormat, echo_json
from ...cliwiring.config import load_config_or_exit
from ...cliwiring.runtime import runtime_of
from ..output import (
    build_graph_json,
    format_mermaid_ascii_graph,
    format_mermaid_graph,
    format_rich_tree_graph,
)
from . import app


class GraphFormat(enum.StrEnum):
    RICH_TREE = "rich-tree"
    MERMAID_ASCII = "mermaid-ascii"
    MERMAID = "mermaid"


@app.command("graph")
def graph_cmd(
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
    format: Annotated[
        GraphFormat,
        typer.Option("--format", "-f", help="Graph format (human output only)"),
    ] = GraphFormat.RICH_TREE,
) -> None:
    """Display the backup chain as a graph."""
    cfg = load_config_or_exit(runtime_of(ctx), config, output)
    match output:
        case OutputFormat.JSON:
            echo_json(build_graph_json(cfg))
        case OutputFormat.HUMAN:
            match format:
                case GraphFormat.RICH_TREE:
                    Console().print(format_rich_tree_graph(cfg))
                case GraphFormat.MERMAID_ASCII:
                    Console().print(format_mermaid_ascii_graph(cfg))
                case GraphFormat.MERMAID:
                    # soft_wrap: lines wider than the terminal stay whole,
                    # since the output is piped into a mermaid renderer.
                    Console().print(format_mermaid_graph(cfg), soft_wrap=True)
