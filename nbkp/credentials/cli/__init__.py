"""The ``credentials`` command group: checks on the credential store."""

import typer

from nbkp.credentials.cli.keyring_status_cmd import keyring_status_cmd

app = typer.Typer(
    name="credentials",
    help="Credential management commands",
    no_args_is_help=True,
)

app.command("keyring-status")(keyring_status_cmd)
