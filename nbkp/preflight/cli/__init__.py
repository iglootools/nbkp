"""Preflight CLI sub-app."""

import typer

app = typer.Typer(
    name="preflight",
    help="Pre-flight check commands",
    no_args_is_help=True,
)

from . import check_cmd as _check_cmd  # noqa: E402, F401
from . import troubleshoot_cmd as _troubleshoot_cmd  # noqa: E402, F401
