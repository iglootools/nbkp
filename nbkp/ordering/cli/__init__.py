"""Ordering CLI sub-app."""

import typer

app = typer.Typer(
    name="ordering",
    help="Sync dependency graph commands",
    no_args_is_help=True,
)

from . import graph_cmd as _graph_cmd  # noqa: E402, F401
