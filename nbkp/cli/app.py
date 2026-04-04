"""CLI app: root Typer app with domain sub-apps wired in."""

import importlib.metadata
from typing import Annotated, Optional

import typer

from ..config.cli import app as config_app
from ..credentials.cli import app as credentials_app
from ..demo import app as demo_app
from ..disks.cli import app as disks_app
from ..ordering.cli import app as ordering_app
from ..preflight.cli import app as preflight_app
from ..run.cli.run_cmd import run as run_fn
from ..sh.cli.sh_cmd import sh as sh_fn
from ..snapshots.cli import app as snapshots_app


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


# ── Sub-apps ────────────────────────────────────────────────────────
app.add_typer(config_app)
app.add_typer(credentials_app)
app.add_typer(disks_app)
app.add_typer(ordering_app)
app.add_typer(preflight_app)
app.add_typer(snapshots_app)
app.add_typer(
    demo_app,
    name="demo",
    help="Demo CLI: sample output rendering and seed data",
)

# ── Top-level commands ──────────────────────────────────────────────
app.command()(run_fn)
app.command(name="sh")(sh_fn)
