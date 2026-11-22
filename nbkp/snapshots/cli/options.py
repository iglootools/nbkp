"""Options the ``snapshots`` commands share, each declared once."""

from typing import Annotated

import typer

from nbkp.policy import Strictness

STRICTNESS_OPTION = Annotated[
    Strictness,
    typer.Option(
        "--strictness",
        "-S",
        help=(
            "How to handle preflight errors:"
            " ignore-none (inactive syncs fail),"
            " ignore-inactive (skip expected-inactive, default),"
            " ignore-all (skip syncs with preflight errors)"
        ),
    ),
]
