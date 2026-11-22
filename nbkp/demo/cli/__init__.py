"""The ``nbkp-demo`` command-line app."""

import typer

from nbkp.demo.cli.output_cmd import output_cmd
from nbkp.demo.cli.seed_cmd import seed_cmd

app = typer.Typer(
    name="nbkp-demo",
    help="NBKP demo CLI",
    no_args_is_help=True,
)

# Registration order is the order commands are listed in `--help` and in the
# generated CLI reference.
app.command("output")(output_cmd)
app.command("seed")(seed_cmd)
