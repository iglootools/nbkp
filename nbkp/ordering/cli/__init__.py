import typer

app = typer.Typer(
    name="ordering",
    help="Sync dependency graph commands",
    no_args_is_help=True,
)

# Imported to register its command on the Typer app.
from nbkp.ordering.cli import graph_cmd as graph_cmd
