"""Options the ``disks`` commands share, each declared once."""

from typing import Annotated

import typer

VOLUME_OPTION = Annotated[
    list[str] | None,
    typer.Option("--volume", "-v", help="Volume name(s) to include"),
]
