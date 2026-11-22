"""The ``nbkp ordering graph`` command."""

import enum
from typing import Annotated

import typer
from rich.console import Console

from nbkp.clidisplay import OutputFormat, echo_json
from nbkp.cliwiring.config import load_config_or_exit
from nbkp.cliwiring.options import CONFIG_OPTION, OUTPUT_OPTION
from nbkp.cliwiring.runtime import runtime_of
from nbkp.ordering.output import (
    build_graph_json,
    format_mermaid_ascii_graph,
    format_mermaid_graph,
    format_rich_tree_graph,
)


class GraphFormat(enum.StrEnum):
    """How ``nbkp ordering graph`` draws the graph in human output."""

    RICH_TREE = "rich-tree"
    MERMAID_ASCII = "mermaid-ascii"
    MERMAID = "mermaid"


def graph_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    graph_format: Annotated[
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
            match graph_format:
                case GraphFormat.RICH_TREE:
                    Console().print(format_rich_tree_graph(cfg))
                case GraphFormat.MERMAID_ASCII:
                    Console().print(format_mermaid_ascii_graph(cfg))
                case GraphFormat.MERMAID:
                    # soft_wrap: lines wider than the terminal stay whole,
                    # since the output is piped into a mermaid renderer.
                    Console().print(format_mermaid_graph(cfg), soft_wrap=True)
