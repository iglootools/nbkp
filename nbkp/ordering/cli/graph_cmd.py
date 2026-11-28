"""The ``nbkp ordering graph`` command."""

import enum
from typing import Annotated

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit
from nbkp.cliwiring.options import CONFIG_OPTION
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.runtime import runtime_of
from nbkp.ordering.output import (
    build_graph_json,
    format_mermaid_ascii_graph,
    format_mermaid_graph,
    format_rich_tree_graph,
)


class GraphOutput(enum.StrEnum):
    """
    What ``nbkp ordering graph`` prints.

    ``human`` and ``json`` as every command; the graph also has two drawings
    meant for other tools, so they are output formats too rather than a
    second selector.
    """

    HUMAN = "human"
    JSON = "json"
    MERMAID = "mermaid"
    MERMAID_ASCII = "mermaid-ascii"


def graph_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: Annotated[
        GraphOutput,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Output format: human (a tree), json, mermaid (Mermaid syntax)"
                " or mermaid-ascii (the Mermaid graph drawn as ASCII art)"
            ),
        ),
    ] = GraphOutput.HUMAN,
) -> None:
    """Display the backup chain as a graph."""
    cfg = load_config_or_exit(
        runtime_of(ctx),
        config,
        OutputFormat.JSON if output is GraphOutput.JSON else OutputFormat.HUMAN,
    )
    match output:
        case GraphOutput.JSON:
            echo_json(build_graph_json(cfg))
        case GraphOutput.HUMAN:
            Console().print(format_rich_tree_graph(cfg))
        case GraphOutput.MERMAID_ASCII:
            Console().print(format_mermaid_ascii_graph(cfg))
        case GraphOutput.MERMAID:
            # soft_wrap: lines wider than the terminal stay whole,
            # since the output is piped into a mermaid renderer.
            Console().print(format_mermaid_graph(cfg), soft_wrap=True)
