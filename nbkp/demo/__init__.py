"""NBKP demo CLI: sample output rendering and seed data."""

from .cli import app

__all__ = ["app", "main"]


def main() -> None:
    """Demo CLI entry point."""
    app()
