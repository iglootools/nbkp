"""NBKP demo CLI: sample output rendering and seed data."""

from nbkp.demo.cli import app

__all__ = ["app", "main"]


def main() -> None:
    """Run the ``nbkp-demo`` command-line app."""
    app()
