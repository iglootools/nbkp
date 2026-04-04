"""CLI keyring-status command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ...clihelpers import OutputFormat
from ...config.clihelpers import load_config_or_exit
from ...config import Config
from .. import CredentialError, retrieve_passphrase
from . import app


def _collect_passphrase_ids(cfg: Config) -> dict[str, list[str]]:
    """Map passphrase-id to list of volume slugs that use it."""
    result: dict[str, list[str]] = {}
    for vol in cfg.volumes.values():
        mount = getattr(vol, "mount", None)
        if mount is not None and mount.encryption is not None:
            result.setdefault(mount.encryption.passphrase_id, []).append(vol.slug)
    return result


@app.command("keyring-status")
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
