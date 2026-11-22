import typer

app = typer.Typer(
    name="preflight",
    help="Pre-flight check commands",
    no_args_is_help=True,
)

# Import order determines the order commands are listed in `--help` and in the
# generated CLI reference. Keep isort out of it.
# isort: off
from nbkp.preflight.cli import check_cmd as check_cmd
from nbkp.preflight.cli import troubleshoot_cmd as troubleshoot_cmd

# isort: on
