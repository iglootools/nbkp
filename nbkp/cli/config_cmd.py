"""CLI config show and graph commands."""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..credentials import CredentialError, retrieve_passphrase
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


@config_app.command()
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


@config_app.command("setup-auth")
def setup_auth(
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


def _collect_passphrase_ids(cfg: Config) -> dict[str, list[str]]:
    """Map passphrase-id → list of volume slugs that use it."""
    result: dict[str, list[str]] = {}
    for vol in cfg.volumes.values():
        mount = getattr(vol, "mount", None)
        if mount is not None and mount.encryption is not None:
            result.setdefault(mount.encryption.passphrase_id, []).append(vol.slug)
    return result


@config_app.command("keyring-status")
def keyring_status(
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
    """Check whether LUKS passphrases are available in the credential store."""
    cfg = load_config_or_exit(config)
    passphrase_ids = _collect_passphrase_ids(cfg)

    if not passphrase_ids:
        typer.echo("No encrypted volumes found.", err=True)
        raise typer.Exit(0)

    statuses: dict[str, tuple[bool, str | None]] = {}
    for pid in sorted(passphrase_ids):
        try:
            retrieve_passphrase(pid, cfg.credential_provider, cfg.credential_command)
            statuses[pid] = (True, None)
        except CredentialError as e:
            statuses[pid] = (False, str(e))

    match output:
        case OutputFormat.JSON:
            typer.echo(
                json.dumps(
                    {
                        "provider": cfg.credential_provider.value,
                        "passphrases": {
                            pid: {
                                "available": available,
                                "volumes": sorted(passphrase_ids[pid]),
                                **({"error": error} if error else {}),
                            }
                            for pid, (available, error) in statuses.items()
                        },
                    },
                    indent=2,
                )
            )
        case OutputFormat.HUMAN:
            console = Console()
            table = Table(title="Credential Status:")
            table.add_column("Passphrase ID")
            table.add_column("Volumes")
            table.add_column("Provider")
            table.add_column("Status")

            for pid, (available, error) in statuses.items():
                volumes_str = ", ".join(sorted(passphrase_ids[pid]))
                if available:
                    status = Text("\u2713 available", style="green")
                else:
                    status = Text(
                        f"\u2717 missing ({error})" if error else "\u2717 missing",
                        style="red",
                    )
                table.add_row(
                    pid,
                    volumes_str,
                    cfg.credential_provider.value,
                    status,
                )

            console.print(table)
