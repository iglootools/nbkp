"""Credentials CLI sub-app."""

import typer

app = typer.Typer(
    name="credentials",
    help="Credential management commands",
    no_args_is_help=True,
)

from . import keyring_status_cmd as _keyring_status_cmd  # noqa: E402, F401
