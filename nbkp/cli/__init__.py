"""Typer CLI: run and status commands."""

from .app import app

# Import command modules to register their @app.command() decorators.
from . import check_cmd as _check_cmd  # noqa: F401
from . import config_cmd as _config_cmd  # noqa: F401
from . import prune_cmd as _prune_cmd  # noqa: F401
from . import run_cmd as _run_cmd  # noqa: F401
from . import sh_cmd as _sh_cmd  # noqa: F401
from . import troubleshoot_cmd as _troubleshoot_cmd  # noqa: F401
from . import volumes_cmd as _volumes_cmd  # noqa: F401

__all__ = ["app", "main"]


def main() -> None:
    """Main CLI entry point."""
    app()
