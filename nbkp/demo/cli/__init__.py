import typer
from rich.console import Console

app = typer.Typer(
    name="nbkp-demo",
    help="NBKP demo CLI",
    no_args_is_help=True,
)

console = Console()

# Import order determines the order commands are listed in `--help`.
# Keep isort out of it.
# isort: off
from . import output_cmd as output_cmd
from . import seed_cmd as seed_cmd

# isort: on
