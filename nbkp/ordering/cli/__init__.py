"""The ``nbkp ordering`` command group."""

import typer

from nbkp.ordering.cli.graph_cmd import graph_cmd

app = typer.Typer(
    name="ordering",
    help="Sync dependency graph commands",
    no_args_is_help=True,
)

app.command("graph")(graph_cmd)
