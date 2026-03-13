"""Demo CLI app definition."""

import typer
from rich.console import Console

app = typer.Typer(
    name="nbkp-demo",
    help="NBKP demo CLI",
    no_args_is_help=True,
)

console = Console()
