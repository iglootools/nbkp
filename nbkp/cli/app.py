"""CLI app and sub-app definitions."""

import importlib.metadata
from typing import Annotated, Optional

import typer

from ..democli import app as demo_app


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(importlib.metadata.version("nbkp"))
        raise typer.Exit()


app = typer.Typer(
    name="nbkp",
    help="Nomad Backup",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def _main(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    pass


config_app = typer.Typer(
    name="config",
    help="Configuration commands",
    no_args_is_help=True,
)
app.add_typer(config_app)

volumes_app = typer.Typer(
    name="volumes",
    help="Volume mount management commands",
    no_args_is_help=True,
)
app.add_typer(volumes_app)

app.add_typer(
    demo_app,
    name="demo",
    help="Demo CLI: sample output rendering and seed data",
)
