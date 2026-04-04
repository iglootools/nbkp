"""Snapshots CLI sub-app."""

import typer

app = typer.Typer(
    name="snapshots", help="Snapshot management commands", no_args_is_help=True
)

from . import prune_cmd as _prune_cmd  # noqa: E402, F401
