import typer

app = typer.Typer(
    name="snapshots", help="Snapshot management commands", no_args_is_help=True
)

# Import order determines the order commands are listed in `--help` and in the
# generated CLI reference. Keep isort out of it.
# isort: off
from . import prune_cmd as prune_cmd
from . import show_cmd as show_cmd

# isort: on
