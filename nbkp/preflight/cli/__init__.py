"""The ``nbkp preflight`` sub-app: the ``check`` and ``troubleshoot`` commands."""

import typer

from nbkp.preflight.cli.check_cmd import check_cmd
from nbkp.preflight.cli.troubleshoot_cmd import troubleshoot_cmd

app = typer.Typer(
    name="preflight",
    help="Pre-flight check commands",
    no_args_is_help=True,
)

# Registration order is the order commands are listed in `--help` and in the
# generated CLI reference.
app.command("check")(check_cmd)
app.command("troubleshoot")(troubleshoot_cmd)
