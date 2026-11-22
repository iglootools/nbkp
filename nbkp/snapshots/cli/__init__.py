"""The ``nbkp snapshots`` command group: ``prune`` and ``show``."""

import typer

from nbkp.snapshots.cli.prune_cmd import prune_cmd
from nbkp.snapshots.cli.show_cmd import show_cmd

app = typer.Typer(
    name="snapshots",
    help="Snapshot management commands",
    no_args_is_help=True,
)

# Registration order is the order commands are listed in `--help` and in the
# generated CLI reference.
app.command("prune")(prune_cmd)
app.command("show")(show_cmd)
