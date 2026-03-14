"""CLI config show and graph commands."""

from __future__ import annotations

import enum
import json
from typing import Annotated, Optional

import typer

from ..config import resolve_all_endpoints
from ..ordering.output import (
    build_graph_json,
    print_mermaid_ascii_graph,
    print_mermaid_graph,
    print_rich_tree_graph,
)
from ..output import (
    OutputFormat,
    print_human_config,
)
from .app import config_app
from .common import load_config_or_exit


class GraphFormat(str, enum.Enum):
    """Graph output format."""

    RICH_TREE = "rich-tree"
    MERMAID_ASCII = "mermaid-ascii"
    MERMAID = "mermaid"


@config_app.command()
def show(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
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
            typer.echo(json.dumps(cfg.model_dump(by_alias=True), indent=2))
        case OutputFormat.HUMAN:
            resolved = resolve_all_endpoints(cfg)
            print_human_config(cfg, resolved_endpoints=resolved)


@config_app.command()
def graph(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
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
