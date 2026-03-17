"""CLI config show and graph commands."""

from __future__ import annotations

import enum
import json
from typing import Annotated, Optional

import typer

from ..remote.resolution import resolve_all_endpoints
from ..mount.auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH, generate_auth_rules
from ..ordering.output import (
    build_graph_json,
    print_mermaid_ascii_graph,
    print_mermaid_graph,
    print_rich_tree_graph,
)
from ..config.output import print_human_config
from .app import config_app
from .common import OutputFormat, load_config_or_exit


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
            typer.echo(json.dumps(cfg.model_dump(by_alias=True, mode="json"), indent=2))
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


@config_app.command("setup-auth")
def setup_auth(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    user: Annotated[
        str,
        typer.Option("--user", "-u", help="System user for auth rules"),
    ] = "ubuntu",
) -> None:
    """Generate polkit and sudoers configuration for mount management."""
    cfg = load_config_or_exit(config)
    rules = generate_auth_rules(cfg, user)

    if rules.polkit is None and rules.sudoers is None:
        typer.echo("No volumes with mount config found.", err=True)
        raise typer.Exit(0)

    if rules.polkit is not None:
        typer.echo("# polkit rules")
        typer.echo(f"# Install to: {POLKIT_RULES_PATH}")
        typer.echo()
        typer.echo(rules.polkit)

    if rules.sudoers is not None:
        typer.echo("# sudoers rules")
        typer.echo(f"# Install with: sudo visudo -f {SUDOERS_RULES_PATH}")
        typer.echo()
        typer.echo(rules.sudoers)
