import typer

app = typer.Typer(
    name="credentials",
    help="Credential management commands",
    no_args_is_help=True,
)

# Imported to register its command on the Typer app.
from nbkp.credentials.cli import keyring_status_cmd as keyring_status_cmd
