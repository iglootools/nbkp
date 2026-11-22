"""The ``nbkp config`` command group."""

import typer

from nbkp.config.cli.show_cmd import show_cmd

app = typer.Typer(name="config", help="Configuration commands", no_args_is_help=True)

app.command("show")(show_cmd)
