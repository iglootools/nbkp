"""NBKP demo CLI: sample output rendering and seed data."""

from .app import app

# Import command modules to register their @app.command() decorators.
from . import output_cmd as _output_cmd  # noqa: F401
from . import seed_cmd as _seed_cmd  # noqa: F401

__all__ = ["app", "main"]


def main() -> None:
    """Demo CLI entry point."""
    app()
