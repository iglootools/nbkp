"""Demo CLI sub-app."""

import typer
from rich.console import Console

app = typer.Typer(
    name="nbkp-demo",
    help="NBKP demo CLI",
    no_args_is_help=True,
)

console = Console()

from . import output_cmd as _output_cmd  # noqa: E402, F401
from . import seed_cmd as _seed_cmd  # noqa: E402, F401
