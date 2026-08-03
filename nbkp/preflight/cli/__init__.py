"""Preflight CLI sub-app."""

import typer

app = typer.Typer(
    name="preflight",
    help="Pre-flight check commands",
    no_args_is_help=True,
)

# Import order determines the order commands are listed in `--help` and in the
# generated CLI reference. Keep isort out of it.
# isort: off
from . import check_cmd as _check_cmd  # noqa: F401
from . import troubleshoot_cmd as _troubleshoot_cmd  # noqa: F401

# isort: on
