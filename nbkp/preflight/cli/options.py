"""Options the ``preflight`` commands share, each declared once."""

from typing import Annotated

import typer

from nbkp.policy import Strictness

# run declares its own --strictness, whose help says what a run skips: see the
# per-command-help exception in docs/guidelines/cli.md.
STRICTNESS_OPTION = Annotated[
    Strictness,
    typer.Option(
        "--strictness",
        "-S",
        help=(
            "Which sync problems make the command exit non-zero:"
            " ignore-none (any), ignore-inactive (all but expected-inactive,"
            " default), ignore-all (none). Problems on SSH endpoints or"
            " volumes no sync uses are shown but never fatal"
        ),
    ),
]
