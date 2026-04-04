"""Config CLI sub-app."""

import typer

app = typer.Typer(name="config", help="Configuration commands", no_args_is_help=True)

from . import show_cmd as _show_cmd  # noqa: E402, F401
