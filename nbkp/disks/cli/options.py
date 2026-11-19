"""Options the ``disks`` commands share, each declared once."""

from typing import Annotated

import typer

# -n is --name here, not --dry-run: see the "-n in disks" exception in
# docs/guidelines.md.
NAME_OPTION = Annotated[
    list[str] | None,
    typer.Option("--name", "-n", help="Volume name(s) to include"),
]
