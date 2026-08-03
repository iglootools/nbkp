"""Demo CLI sub-app."""

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
from . import output_cmd as _output_cmd  # noqa: F401
from . import seed_cmd as _seed_cmd  # noqa: F401

# isort: on
