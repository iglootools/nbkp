import typer

app = typer.Typer(name="config", help="Configuration commands", no_args_is_help=True)

# Imported to register its command on the Typer app.
from . import show_cmd as show_cmd
