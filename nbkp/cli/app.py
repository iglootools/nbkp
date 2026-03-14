"""CLI app and sub-app definitions."""

import typer

from ..democli import app as demo_app

app = typer.Typer(
    name="nbkp",
    help="Nomad Backup",
    no_args_is_help=True,
)

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
