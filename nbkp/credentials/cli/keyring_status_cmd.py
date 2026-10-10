"""CLI keyring-status command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ...clihelpers import OutputFormat, echo_json
from ...config import Config
from ...config.cli.helpers import load_config_or_exit
from .. import (
    PassphrasePrefetch,
    collect_passphrase_ids,
    is_prefetchable,
    prefetch_passphrases,
    retrieve_passphrase,
)
from . import app


@app.command("keyring-status")
def keyring_status(
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
    """Check whether LUKS passphrases are available in the credential store."""
    cfg = load_config_or_exit(config, output)
    # Same sweep as the one run/mount perform before touching any device, so
    # this command triggers exactly the approval prompts a run would — and,
    # like it, never prompts interactively for the ``prompt`` provider.
    results = prefetch_passphrases(
        cfg,
        lambda pid: retrieve_passphrase(
            pid, cfg.credential_provider, cfg.credential_command
        ),
    )
    match output:
        case OutputFormat.JSON:
            echo_json(_status_json(cfg, results))
        case OutputFormat.HUMAN:
            _print_human_status(cfg, results)


def _status_json(cfg: Config, results: list[PassphrasePrefetch]) -> dict[str, Any]:
    """JSON report; ``checked`` is false when the provider is not swept."""
    checked = is_prefetchable(cfg.credential_provider)
    by_id = {r.passphrase_id: r for r in results}
    return {
        "provider": cfg.credential_provider.value,
        "checked": checked,
        "passphrases": {
            pid: {
                "volumes": sorted(volumes),
                **(_result_json(by_id[pid]) if pid in by_id else {}),
            }
            for pid, volumes in sorted(collect_passphrase_ids(cfg).items())
        },
    }


def _result_json(result: PassphrasePrefetch) -> dict[str, Any]:
    return {
        "available": result.success,
        **({"error": result.detail} if result.detail else {}),
        **({"reason": result.reason.value} if result.reason else {}),
    }


def _print_human_status(cfg: Config, results: list[PassphrasePrefetch]) -> None:
    if not collect_passphrase_ids(cfg):
        typer.echo("No encrypted volumes found.", err=True)
    elif not is_prefetchable(cfg.credential_provider):
        typer.echo(
            f"Credential provider '{cfg.credential_provider.value}' asks for each"
            " passphrase interactively at unlock time; there is nothing to check"
            " ahead of a run.",
            err=True,
        )
    else:
        table = Table(title="Credential Status:")
        table.add_column("Passphrase ID")
        table.add_column("Volumes")
        table.add_column("Provider")
        table.add_column("Status")
        for result in results:
            # Passphrase ids, slugs and error details are free-form: Text keeps
            # a bracket in them from being read as a style tag.
            table.add_row(
                Text(result.passphrase_id),
                Text(", ".join(result.volumes)),
                cfg.credential_provider.value,
                _status_text(result),
            )
        Console().print(table)


def _status_text(result: PassphrasePrefetch) -> Text:
    if result.success:
        return Text("✓ available", style="green")
    else:
        return Text(
            f"✗ missing ({result.detail})" if result.detail else "✗ missing",
            style="red",
        )
