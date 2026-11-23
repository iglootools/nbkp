"""The root ``nbkp`` Typer app, with every command group attached."""

import importlib.metadata
from typing import Annotated

import typer

from nbkp.cliwiring.runtime import runtime_of
from nbkp.config.cli import app as config_app
from nbkp.credentials.cli import app as credentials_app
from nbkp.demo.cli import app as demo_app
from nbkp.disks.cli import app as disks_app
from nbkp.ordering.cli import app as ordering_app
from nbkp.preflight.cli import app as preflight_app
from nbkp.remote.pool import connection_scope
from nbkp.run.cli.run_cmd import run_cmd
from nbkp.sh.cli.sh_cmd import sh_cmd
from nbkp.snapshots.cli import app as snapshots_app


def _version_callback(*, value: bool) -> None:
    if value:
        typer.echo(importlib.metadata.version("nbkp"))
        raise typer.Exit


app = typer.Typer(
    name="nbkp",
    help="Nomad Backup",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def app_callback(
    ctx: typer.Context,
    version: Annotated[  # noqa: ARG001 -- read by its eager callback, for Typer
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Install the host I/O and the shared SSH connections for the subcommand."""
    # The subcommand runs with the real host I/O, unless the caller passed its
    # own Runtime as the context object (tests do, with fakes).
    runtime_of(ctx)
    # Every remote command of the invoked subcommand shares one SSH connection
    # per host, closed when the subcommand returns or fails, after its own
    # cleanup (unmounting included).
    ctx.with_resource(connection_scope())


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

app.command(name="run")(run_cmd)
app.command(name="sh")(sh_cmd)
